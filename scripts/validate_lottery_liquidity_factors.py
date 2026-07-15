"""彩票 (MAX) + 非流动性 (Amihud) 因子的正式检验（T2 阶段）。

两个新因子（口径与 handoff 中一致）：
    max_20    近 20 日最大单日涨幅（收盘/前收-1 的 rolling max），预期负 IC。
              高 MAX 的"彩票股"事后系统性跑输——这是学界最稳的一批异象之一。
    amihud_20 近 20 日 mean(|日收益| / 当日成交额)，预期正 IC。
              度量单位成交额撬动的价格冲击，越大代表越不流动，理论上有非流动性溢价。

流程与 scripts/validate_stock_factors.py 完全一致（同一批调仓日、同一执行池口径、
同一趋势硬过滤子池），保证结论直接可比。此外额外报告新因子与生产中三个打分因子
（rs / nh_252 / vol_ratio）的截面秩相关均值，作为"低相关闸门"（<0.5）的输入。

闸门（写在 factor_enhancement_handoff.md 的 T2 条目里）：
    |t| >= 2  且  与现有三因子截面相关 < 0.5   →  进入 T2b 接入 stocks.py
    否则收工，把结论写进 outputs/factor_checks/。

用法:
    python3 scripts/validate_lottery_liquidity_factors.py
    python3 scripts/validate_lottery_liquidity_factors.py --label sw1 --industry-source sw1
"""
from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl
from scipy import stats

MIN_LISTED_BARS = 252
N_QUANTILES = 5
BIAS_MAX = 0.25
NH_MIN = 0.80
VOL_CAP = 3.0
AMIHUD_WIN = 20
MAX_WIN = 20
AMIHUD_MIN_PERIODS = 15


def load_stock_features(bars_path: Path) -> pl.DataFrame:
    """逐日逐股滚动特征。所有滚动窗口都只用当日及以前的数据，不引入未来。

    turnover 是"成交额"（元），停牌日通常 = 0；此时 |ret|/turnover 会爆炸，
    先把 turnover<=0 的位记为 null，让 rolling_mean 自动跳过（min_periods 放宽到 15）。
    """
    bars = pl.read_parquet(
        bars_path, columns=["datetime", "vt_symbol", "close", "high", "turnover"]
    ).with_columns(pl.col("datetime").cast(pl.Date).alias("d")).sort(["vt_symbol", "d"])

    over = {"partition_by": "vt_symbol"}
    daily_ret = (pl.col("close") / pl.col("close").shift(1).over(**over) - 1)
    turnover_pos = pl.when(pl.col("turnover") > 0).then(pl.col("turnover")).otherwise(None)
    illiq_daily = (daily_ret.abs() / turnover_pos)

    return bars.with_columns(
        daily_ret.alias("ret_1"),
        (pl.col("close") / pl.col("close").shift(20).over(**over) - 1).alias("ret_20"),
        (pl.col("close") / pl.col("close").shift(60).over(**over) - 1).alias("ret_60"),
        (pl.col("close") / pl.col("close").shift(5).over(**over) - 1).alias("ret_5"),
        (pl.col("close") / pl.col("high").rolling_max(252).over(**over)).alias("nh_252"),
        (
            pl.col("turnover").rolling_mean(20).over(**over)
            / pl.col("turnover").rolling_mean(60).over(**over)
        ).clip(upper_bound=VOL_CAP).alias("vol_ratio"),
        pl.col("close").rolling_mean(20).over(**over).alias("ma20"),
        pl.col("close").rolling_mean(60).over(**over).alias("ma60"),
        pl.col("close").rolling_mean(120).over(**over).alias("ma120"),
        pl.col("close").rolling_mean(20).shift(5).over(**over).alias("ma20_lag"),
        pl.int_range(pl.len()).over("vt_symbol").alias("listed_bars"),
    ).with_columns(
        daily_ret.rolling_max(MAX_WIN).over(**over).alias("max_20"),
        illiq_daily.rolling_mean(AMIHUD_WIN, min_periods=AMIHUD_MIN_PERIODS)
        .over(**over)
        .alias("amihud_20"),
    )


def load_industry_momentum(bars: pl.DataFrame, members_path: Path, prefix: str):
    """行业等权动量（20 日 / 60 日），逐日。返回: (momentum, members)。"""
    members = (
        pl.read_parquet(members_path)
        .filter(pl.col("sector").str.starts_with(prefix))
        .select(pl.col("sector").alias("industry"), "vt_symbol")
        .unique()
    )
    daily = (
        bars.select("vt_symbol", "d", "close")
        .sort(["vt_symbol", "d"])
        .with_columns((pl.col("close") / pl.col("close").shift(1).over("vt_symbol") - 1).alias("ret"))
        .drop_nulls("ret")
        .join(members, on="vt_symbol", how="inner")
        .group_by(["industry", "d"])
        .agg(pl.col("ret").mean().alias("ind_ret"))
        .sort(["industry", "d"])
        .with_columns((pl.col("ind_ret") + 1).cum_prod().over("industry").alias("idx"))
    )
    momentum = daily.with_columns(
        (pl.col("idx") / pl.col("idx").shift(20).over("industry") - 1).alias("ind_mom_20"),
        (pl.col("idx") / pl.col("idx").shift(60).over("industry") - 1).alias("ind_mom_60"),
    )
    return momentum.select("industry", "d", "ind_mom_20", "ind_mom_60"), members


def build_period_ends(trade_dates: list, freq: str) -> list:
    frame = pl.DataFrame({"d": trade_dates}).sort("d")
    if freq == "weekly":
        key = pl.col("d").dt.iso_year().cast(pl.String) + "-W" + pl.col("d").dt.week().cast(pl.String)
    else:
        key = pl.col("d").dt.strftime("%Y-%m")
    ends = (
        frame.with_columns(key.alias("k")).group_by("k").agg(pl.col("d").max()).sort("d")
        .get_column("d").to_list()
    )
    return ends[:-1]


def build_snapshot(
    features: pl.DataFrame,
    industry_momentum: pl.DataFrame,
    members: pl.DataFrame,
    universe: pl.DataFrame,
    period_ends: list,
) -> pl.DataFrame:
    """调仓日截面：因子 + 硬过滤标记 + 下一持有期收益（下期缺行情记 null 剔除）。"""
    order = {d: i for i, d in enumerate(period_ends)}
    snap = (
        features.filter(pl.col("d").is_in(period_ends))
        .join(members, on="vt_symbol", how="inner")
        .join(industry_momentum, on=["industry", "d"], how="left")
        .join(universe, left_on=["d", "vt_symbol"], right_on=["datetime", "vt_symbol"], how="left")
        .with_columns(
            pl.col("in_execution").fill_null(False),
            (pl.col("ret_20") - pl.col("ind_mom_20")).alias("rs_20"),
            (pl.col("ret_60") - pl.col("ind_mom_60")).alias("rs_60"),
            pl.col("d").replace_strict(order, return_dtype=pl.Int64).alias("pidx"),
            (
                (pl.col("ma20") > pl.col("ma60")) & (pl.col("ma60") > pl.col("ma120"))
                & (pl.col("ma20") > pl.col("ma20_lag"))
                & ((pl.col("close") / pl.col("ma20") - 1) <= BIAS_MAX)
                & (pl.col("nh_252") >= NH_MIN)
            ).fill_null(False).alias("trend_ok"),
        )
        .sort(["vt_symbol", "pidx"])
        .with_columns(
            pl.when(pl.col("pidx").shift(-1).over("vt_symbol") == pl.col("pidx") + 1)
            .then(pl.col("close").shift(-1).over("vt_symbol") / pl.col("close") - 1)
            .otherwise(None)
            .alias("fwd_ret")
        )
    )
    return snap.filter(
        pl.col("fwd_ret").is_not_null()
        & pl.col("in_execution")
        & (pl.col("listed_bars") >= MIN_LISTED_BARS)
    )


def evaluate_factor(pool: pl.DataFrame, factor: str, periods_per_year: int) -> dict:
    """单因子的逐期 Rank IC 与 5 分层统计。"""
    data = pool.select("d", factor, "fwd_ret").drop_nulls()

    ics = []
    for (_, group) in data.group_by("d"):
        if len(group) >= 30:
            ic, _ = stats.spearmanr(group[factor].to_list(), group["fwd_ret"].to_list())
            ics.append(ic)
    s = pl.Series(ics)
    n = len(s)
    ic_mean, ic_std = s.mean(), s.std()

    ranked = data.with_columns(
        ((pl.col(factor).rank("ordinal").over("d") - 1) * N_QUANTILES
         // pl.col(factor).count().over("d")).alias("q")
    )
    q_means = (
        ranked.group_by(["d", "q"]).agg(pl.col("fwd_ret").mean().alias("q_ret"))
        .group_by("q").agg(pl.col("q_ret").mean().alias("mean_ret")).sort("q")
        .get_column("mean_ret").to_list()
    )
    top = ranked.filter(pl.col("q") == N_QUANTILES - 1).group_by("d").agg(pl.col("fwd_ret").mean().alias("top"))
    bot = ranked.filter(pl.col("q") == 0).group_by("d").agg(pl.col("fwd_ret").mean().alias("bot"))
    spread = top.join(bot, on="d").with_columns((pl.col("top") - pl.col("bot")).alias("sp")).get_column("sp")

    monotone_up = all(q_means[i] <= q_means[i + 1] for i in range(len(q_means) - 1))
    monotone_dn = all(q_means[i] >= q_means[i + 1] for i in range(len(q_means) - 1))
    return {
        "n_periods": n,
        "ic_mean": ic_mean,
        "icir": ic_mean / ic_std if ic_std else float("nan"),
        "t_stat": ic_mean / ic_std * (n ** 0.5) if ic_std else float("nan"),
        "pct_positive": (s > 0).mean(),
        "q_means": q_means,
        "spread_mean": spread.mean(),
        "spread_t": spread.mean() / spread.std() * (len(spread) ** 0.5) if spread.std() else float("nan"),
        "spread_ann": spread.mean() * periods_per_year,
        "monotone": "up" if monotone_up else ("down" if monotone_dn else "no"),
    }


def cross_section_rank_corr(pool: pl.DataFrame, a: str, b: str) -> float:
    """逐日截面秩相关均值（Spearman）。"""
    data = pool.select("d", a, b).drop_nulls()
    corrs = []
    for (_, g) in data.group_by("d"):
        if len(g) >= 30:
            r, _ = stats.spearmanr(g[a].to_list(), g[b].to_list())
            corrs.append(r)
    return float(pl.Series(corrs).mean()) if corrs else float("nan")


def _fmt_row(name: str, r: dict) -> str:
    q = r["q_means"]
    mono = {"up": "升", "down": "降", "no": "否"}[r["monotone"]]
    return (
        f"| {name} | {r['ic_mean']:+.3f} | {r['icir']:+.2f} | {r['t_stat']:+.2f} "
        f"| {r['pct_positive']:.0%} | {q[0] * 100:+.2f}% | {q[2] * 100:+.2f}% | {q[-1] * 100:+.2f}% "
        f"| {r['spread_ann'] * 100:+.1f}% | {r['spread_t']:+.2f} | {mono} |"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", default="data/silver/daily_bars_adjusted.parquet")
    parser.add_argument("--universe", default="data/universe/execution_universe.parquet")
    parser.add_argument("--industry-source", default="gics1", choices=["gics1", "sw1"])
    parser.add_argument("--label", default="gics1")
    parser.add_argument("--output-dir", default="outputs/factor_checks")
    args = parser.parse_args()

    if args.industry_source == "sw1":
        members_path, prefix = Path("data/sector/sw1_members.parquet"), "SW1"
    else:
        members_path, prefix = Path("data/sector/sector_members.parquet"), "GICS1"

    print("计算逐日特征 ...")
    features = load_stock_features(Path(args.bars))
    industry_momentum, members = load_industry_momentum(features, members_path, prefix)
    universe = (
        pl.read_parquet(args.universe, columns=["datetime", "vt_symbol", "in_execution"])
        .with_columns(pl.col("datetime").cast(pl.Date))
    )
    trade_dates = sorted(
        features.filter(
            (pl.col("d") >= universe.get_column("datetime").min())
            & (pl.col("d") <= universe.get_column("datetime").max())
        ).get_column("d").unique().to_list()
    )

    lines = [
        f"# 彩票 (MAX) + 非流动性 (Amihud) 因子检验：{args.label}",
        "",
        f"- 行业口径: {prefix}（相对强度基准）",
        f"- 样本区间: {trade_dates[0]} ~ {trade_dates[-1]}（执行池覆盖范围）",
        f"- 池口径: all = 可交易 + 上市满 {MIN_LISTED_BARS} 根 K 线；"
        f"filtered = 再叠加均线多头 + 乖离<= {BIAS_MAX:.0%} + 距新高 >= {NH_MIN:.0%}",
        f"- 因子定义: max_20 = 近 20 日最大单日涨幅（预期负 IC）；"
        f"amihud_20 = 近 20 日 mean(|日收益| / 成交额)（预期正 IC，min_periods={AMIHUD_MIN_PERIODS}）",
        f"- 前瞻收益 = 当期调仓日收盘到下期调仓日收盘（等比后复权），{N_QUANTILES} 分层",
        "- 局限: 行业成分为静态快照；不含交易成本与可成交性约束；全部为样本内统计",
        "",
    ]

    corr_snapshots: dict[str, pl.DataFrame] = {}

    for freq, rs_col, _confirm, ppy in [
        ("weekly", "rs_20", "ret_5", 52),
        ("monthly", "rs_60", "ret_20", 12),
    ]:
        period_ends = build_period_ends(trade_dates, freq)
        snap = build_snapshot(features, industry_momentum, members, universe, period_ends)

        for pool_name, pool in [("all", snap), ("filtered", snap.filter(pl.col("trend_ok")))]:
            pool_size = pool.group_by("d").len().get_column("len").median()
            lines.append(
                f"## {freq} / {pool_name} 池（{len(period_ends)} 期，池中位 {pool_size:.0f} 只）"
            )
            lines.append("")
            lines.append("| 因子 | IC均值 | ICIR | t值 | IC>0 | Q1 | Q3 | Q5 | 顶底价差年化 | 价差t | 单调 |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
            for factor in ["max_20", "amihud_20"]:
                r = evaluate_factor(pool, factor, ppy)
                row = _fmt_row(factor, r)
                lines.append(row)
                print(row)
            lines.append("")
            corr_snapshots[f"{freq}/{pool_name}"] = pool.select(
                "d", "max_20", "amihud_20", rs_col, "nh_252", "vol_ratio"
            )

    # 因子间截面相关：均值（周频全市场池为主口径，与老报告口径一致）
    lines.append("## 与现有因子的截面秩相关（均值）")
    lines.append("")
    lines.append("| 池 | 新因子 | rs | nh_252 | vol_ratio |")
    lines.append("|---|---|---|---|---|")
    for pool_key, pool in corr_snapshots.items():
        rs_col = "rs_20" if pool_key.startswith("weekly") else "rs_60"
        for new_f in ["max_20", "amihud_20"]:
            r_rs = cross_section_rank_corr(pool, new_f, rs_col)
            r_nh = cross_section_rank_corr(pool, new_f, "nh_252")
            r_vol = cross_section_rank_corr(pool, new_f, "vol_ratio")
            lines.append(
                f"| {pool_key} | {new_f} | {r_rs:+.2f} | {r_nh:+.2f} | {r_vol:+.2f} |"
            )
    lines.append("")

    lines.append("## 闸门（handoff T2）")
    lines.append("")
    lines.append("- 通过条件: |t| >= 2 且 与三个现有因子截面相关绝对值均 < 0.5")
    lines.append("- 通过则进入 T2b：仿 volatility_weight 接入 stocks.py（默认 0 关闭），两档权重回测对照")
    lines.append("- 不通过则记录结论收工，不接入策略")
    lines.append("")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"lottery_liquidity_{args.label}.md"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已写入 {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
