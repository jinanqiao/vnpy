"""个股打分因子的正式检验：Rank IC + 分层回测（006 后续）。

检验对象是策略个股层的三个打分因子和合成分：
    rs        相对强度 = 个股 W 日收益 - 所属行业 W 日等权动量（W: 周频用 20，月频用 60）
    nh_252    距新高   = 收盘价 / 252 日最高价
    vol_ratio 量价     = 20 日均成交额 / 60 日均成交额（cap 3）
    score     合成分   = 0.40*rank(rs) + 0.35*rank(nh) + 0.25*rank(vol)，
              量价排名带方向规则：确认窗口下跌的股票记 0.5 中性分（与策略实现一致）

两个候选池口径：
    all       全市场可交易池（执行池 in_execution + 上市满 252 根 K 线）
    filtered  再叠加策略的趋势硬过滤（均线多头 + 乖离 <= 25% + 距新高 >= 80%），
              即"因子实际参与排名的池子"

方法与 validate_industry_momentum.py 一致：每个调仓日截面算因子与下一持有期收益，
逐期 Rank IC（均值 / ICIR / t 值）+ 5 分层回测（单调性 / 顶底价差显著性）。

用法:
    python3 scripts/validate_stock_factors.py
    python3 scripts/validate_stock_factors.py --label sw1 --industry-source sw1
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
SCORE_WEIGHTS = (0.40, 0.35, 0.25)


def load_stock_features(bars_path: Path) -> pl.DataFrame:
    """逐日逐股滚动特征：动量 / 距新高 / 量比 / 均线 / 上市天数（全部只用当日及以前数据）。"""
    bars = pl.read_parquet(
        bars_path, columns=["datetime", "vt_symbol", "close", "high", "turnover"]
    ).with_columns(pl.col("datetime").cast(pl.Date).alias("d")).sort(["vt_symbol", "d"])

    over = {"partition_by": "vt_symbol"}
    return bars.with_columns(
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
    )


def load_industry_momentum(bars: pl.DataFrame, members_path: Path, prefix: str) -> pl.DataFrame:
    """行业等权动量（20 日 / 60 日），逐日。返回: industry, d, ind_mom_20, ind_mom_60。"""
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
    """调仓日截面：因子 + 硬过滤标记 + 下一持有期收益（下期缺行情的记 null 剔除）。"""
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
            # 下一行必须正好是下一个调仓期，跳期（长期停牌/退市中途消失）记 null
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


def add_score(pool: pl.DataFrame, rs_col: str, confirm_col: str) -> pl.DataFrame:
    """按策略口径在池内合成总分（含量价方向规则：确认窗口下跌记 0.5 中性分）。"""
    w_rs, w_nh, w_vol = SCORE_WEIGHTS
    pct = lambda c: (pl.col(c).rank("average").over("d") / pl.col(c).count().over("d"))  # noqa: E731
    rising = pl.col(confirm_col) > 0
    pool = pool.with_columns(
        pct(rs_col).alias("rank_rs"),
        pct("nh_252").alias("rank_nh"),
        pl.when(rising)
        .then(pl.col("vol_ratio").rank("average").over(["d", rising]) / pl.col("vol_ratio").count().over(["d", rising]))
        .otherwise(0.5)
        .alias("rank_vol"),
    )
    return pool.with_columns(
        (w_rs * pl.col("rank_rs") + w_nh * pl.col("rank_nh") + w_vol * pl.col("rank_vol")).alias("score")
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

    monotone = all(q_means[i] <= q_means[i + 1] for i in range(len(q_means) - 1))
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
        "monotone": monotone,
    }


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
    # 检验窗口对齐执行池覆盖范围（也是策略实际运行的区间）
    trade_dates = sorted(
        features.filter(
            (pl.col("d") >= universe.get_column("datetime").min())
            & (pl.col("d") <= universe.get_column("datetime").max())
        ).get_column("d").unique().to_list()
    )

    lines = [
        f"# 个股打分因子检验：{args.label}",
        "",
        f"- 行业口径: {prefix}（相对强度的基准行业动量）",
        f"- 样本区间: {trade_dates[0]} ~ {trade_dates[-1]}（执行池覆盖范围）",
        f"- 池口径: all = 可交易 + 上市满 {MIN_LISTED_BARS} 根 K 线；"
        f"filtered = 再叠加均线多头 + 乖离<= {BIAS_MAX:.0%} + 距新高 >= {NH_MIN:.0%}",
        f"- 前瞻收益 = 当期调仓日收盘到下期调仓日收盘（等比后复权），{N_QUANTILES} 分层",
        "- 局限: 行业成分为静态快照；不含交易成本与可成交性约束；全部为样本内统计",
        "",
    ]
    print("\n".join(lines))

    for freq, rs_col, confirm_col, ppy in [
        ("weekly", "rs_20", "ret_5", 52),
        ("monthly", "rs_60", "ret_20", 12),
    ]:
        period_ends = build_period_ends(trade_dates, freq)
        snap = build_snapshot(features, industry_momentum, members, universe, period_ends)

        for pool_name, pool in [("all", snap), ("filtered", snap.filter(pl.col("trend_ok")))]:
            pool = add_score(pool, rs_col, confirm_col)
            pool_size = pool.group_by("d").len().get_column("len").median()
            lines.append(f"## {freq} / {pool_name} 池（{len(period_ends)} 期，池中位 {pool_size:.0f} 只）")
            lines.append("")
            lines.append("| 因子 | IC均值 | ICIR | t值 | IC>0 | Q1(弱) | Q3 | Q5(强) | 顶底价差年化 | 价差t | 单调 |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
            for factor in [rs_col, "nh_252", "vol_ratio", "score"]:
                r = evaluate_factor(pool, factor, ppy)
                q = r["q_means"]
                row = (
                    f"| {factor} | {r['ic_mean']:+.3f} | {r['icir']:+.2f} | {r['t_stat']:+.2f} "
                    f"| {r['pct_positive']:.0%} | {q[0]*100:+.2f}% | {q[2]*100:+.2f}% | {q[-1]*100:+.2f}% "
                    f"| {r['spread_ann']*100:+.1f}% | {r['spread_t']:+.2f} | {'是' if r['monotone'] else '否'} |"
                )
                lines.append(row)
                print(row)
            lines.append("")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"stock_factors_{args.label}.md"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已写入 {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
