"""规模因子 + 真换手率因子检验（T5，走 akshare 数据）。

两个新因子：
    size    对数流通市值 = ln(outstanding_share × close_hfq)（越大越是大盘）
    turnover_rate  真换手率（akshare 提供的日度换手率），近 20 日均值

流通市值：outstanding_share 是"当日流通股本"，close_hfq 是"后复权收盘价"，
两者相乘不是历史真实市值（后复权价 ≠ 历史股价），但截面排序对齐当日
（同日大盘/小盘的相对位置是正确的，因为 close_hfq 都做了同尺度调整），
用来做因子排名足够。真实市值时序需要用未复权价 × outstanding_share。

方法与 validate_lottery_liquidity_factors.py 一致：
    Rank IC + 5 分层 + 与生产三因子（rs / nh / vol_ratio）的截面相关
闸门（handoff T2 通用）：
    |t| >= 2 且与现有三因子截面相关 |ρ| < 0.5

**重要**：即使闸门过了，接入前必须做 T2b 的容量鲁棒性对照
（`min_turnover_avg20` 三档），别再被小盘规模溢价冒充 Alpha
（参考 011 Amihud 的教训）。

用法:
    python3 scripts/validate_size_liquidity_factors.py
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import polars as pl
from scipy import stats

MIN_LISTED_BARS = 252
N_QUANTILES = 5
VOL_CAP = 3.0


def load_stock_features_from_dual_bars(
    bars_hfq_path: Path, akshare_path: Path
) -> pl.DataFrame:
    """联合仓库后复权行情 + akshare 数据，产出因子所需特征。

    仓库 bars（daily_bars_all_a_adjusted.parquet）提供 rs/nh/vol 生产因子所需的
    close/high/turnover（成交额）；akshare 数据（daily_bars_outstanding.parquet）
    提供 outstanding_share 和 turnover_rate。两者按 (datetime, vt_symbol) 内联合。
    """
    bars = pl.read_parquet(
        bars_hfq_path, columns=["datetime", "vt_symbol", "close", "high", "turnover"]
    ).with_columns(pl.col("datetime").cast(pl.Date).alias("d")).sort(["vt_symbol", "d"])

    ak = pl.read_parquet(
        akshare_path, columns=["datetime", "vt_symbol", "close_hfq", "outstanding_share", "turnover_rate"]
    ).with_columns(pl.col("datetime").cast(pl.Date).alias("d"))

    over = {"partition_by": "vt_symbol"}
    features = bars.with_columns(
        (pl.col("close") / pl.col("close").shift(20).over(**over) - 1).alias("ret_20"),
        (pl.col("close") / pl.col("close").shift(60).over(**over) - 1).alias("ret_60"),
        (pl.col("close") / pl.col("high").rolling_max(252).over(**over)).alias("nh_252"),
        (
            pl.col("turnover").rolling_mean(20).over(**over)
            / pl.col("turnover").rolling_mean(60).over(**over)
        ).clip(upper_bound=VOL_CAP).alias("vol_ratio"),
        pl.int_range(pl.len()).over("vt_symbol").alias("listed_bars"),
    )
    # akshare 数据可能有些股票不覆盖，left join 保留仓库全池
    joined = features.join(
        ak.select("d", "vt_symbol", "outstanding_share", "turnover_rate"),
        on=["d", "vt_symbol"], how="left",
    ).with_columns(
        # 对数流通市值：ln(outstanding_share * close)（用仓库自己的后复权 close，与老因子对齐）
        (pl.col("outstanding_share") * pl.col("close")).log().alias("size"),
        # 真换手率：近 20 日均值
        pl.col("turnover_rate").rolling_mean(20).over(**over).alias("turnover_rate_20"),
    )
    return joined


def load_industry_momentum(bars: pl.DataFrame, members_path: Path, prefix: str):
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


def build_snapshot(features, industry_momentum, members, universe, period_ends):
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
    parser.add_argument("--akshare", default="data/akshare/daily_bars_outstanding.parquet")
    parser.add_argument("--universe", default="data/universe/execution_universe.parquet")
    parser.add_argument("--industry-source", default="gics1", choices=["gics1", "sw1"])
    parser.add_argument("--label", default="gics1")
    parser.add_argument("--output-dir", default="outputs/factor_checks")
    args = parser.parse_args()

    ak_path = Path(args.akshare)
    if not ak_path.exists():
        print(f"[FATAL] akshare 数据不存在: {ak_path}，请先跑 scripts/download_akshare_daily.py")
        return 2

    if args.industry_source == "sw1":
        members_path, prefix = Path("data/sector/sw1_members.parquet"), "SW1"
    else:
        members_path, prefix = Path("data/sector/sector_members.parquet"), "GICS1"

    print("计算逐日特征 ...")
    features = load_stock_features_from_dual_bars(Path(args.bars), ak_path)
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
        f"# 规模 + 真换手率因子检验：{args.label}",
        "",
        f"- 行业口径: {prefix}（相对强度基准）",
        f"- 样本区间: {trade_dates[0]} ~ {trade_dates[-1]}（执行池覆盖范围）",
        f"- 数据源: akshare stock_zh_a_daily（含 outstanding_share + turnover_rate），"
        f"仓库 daily_bars_all_a_adjusted（生产因子 rs/nh/vol_ratio）",
        f"- 因子定义: size = ln(outstanding_share × close)（截面排序）；"
        f"turnover_rate_20 = akshare 日换手率近 20 日均值",
        f"- 前瞻收益 = 当期调仓日收盘到下期调仓日收盘（等比后复权），{N_QUANTILES} 分层",
        "- 局限: outstanding_share 是当日流通股本快照（非 PIT），后复权价与流通股本相乘不是历史真实市值",
        "",
    ]

    corr_snapshots: dict[str, pl.DataFrame] = {}

    for freq, rs_col, ppy in [
        ("weekly", "rs_20", 52),
        ("monthly", "rs_60", 12),
    ]:
        period_ends = build_period_ends(trade_dates, freq)
        snap = build_snapshot(features, industry_momentum, members, universe, period_ends)

        for pool_name, pool in [("all", snap), ]:  # 规模因子通常在全池最有效，先只跑 all
            pool_size = pool.group_by("d").len().get_column("len").median() if pool.height else 0
            lines.append(f"## {freq} / {pool_name} 池（{len(period_ends)} 期，池中位 {pool_size:.0f} 只）")
            lines.append("")
            lines.append("| 因子 | IC均值 | ICIR | t值 | IC>0 | Q1 | Q3 | Q5 | 顶底价差年化 | 价差t | 单调 |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
            for factor in ["size", "turnover_rate_20"]:
                r = evaluate_factor(pool, factor, ppy)
                row = _fmt_row(factor, r)
                lines.append(row)
                print(row)
            lines.append("")
            corr_snapshots[f"{freq}/{pool_name}"] = pool.select(
                "d", "size", "turnover_rate_20", rs_col, "nh_252", "vol_ratio"
            )

    lines.append("## 与现有因子的截面秩相关（均值）")
    lines.append("")
    lines.append("| 池 | 新因子 | rs | nh_252 | vol_ratio |")
    lines.append("|---|---|---|---|---|")
    for pool_key, pool in corr_snapshots.items():
        rs_col = "rs_20" if pool_key.startswith("weekly") else "rs_60"
        for new_f in ["size", "turnover_rate_20"]:
            r_rs = cross_section_rank_corr(pool, new_f, rs_col)
            r_nh = cross_section_rank_corr(pool, new_f, "nh_252")
            r_vol = cross_section_rank_corr(pool, new_f, "vol_ratio")
            lines.append(
                f"| {pool_key} | {new_f} | {r_rs:+.2f} | {r_nh:+.2f} | {r_vol:+.2f} |"
            )
    lines.append("")

    lines.append("## 闸门（handoff T2 / 011 后新增容量约束）")
    lines.append("")
    lines.append("- 通过条件: |t| >= 2 且 与三个现有因子截面相关绝对值均 < 0.5")
    lines.append("- **即使通过闸门，接入前必须做容量鲁棒性对照**（`min_turnover_avg20` 三档），")
    lines.append("  排除 Alpha 只是小盘规模溢价冒充的可能（011 Amihud 教训）")
    lines.append("- 规模因子本身就是小盘 vs 大盘的度量，可预期强相关 —— 若显示小盘赢，")
    lines.append("  优先怀疑是否只是【小盘反转 + 流动性差】的另一种编码")
    lines.append("")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"size_liquidity_{args.label}.md"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已写入 {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
