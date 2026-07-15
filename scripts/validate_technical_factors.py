"""技术指标类选股因子的正式检验：MACD / RSI / 金叉 / 顶底背离（008 后续）。

用户想知道 MACD 金叉、RSI、顶背离、底背离这类经典技术信号加进选股会不会更好。
按项目惯例先不动策略：把它们做成因子，用 Rank IC + 分层/分组检验在
策略的实际持有期（周频 / 月频）上有没有预测力，有效再接进打分。

连续因子（5 分层 + Rank IC）:
    rsi_14      经典 14 日 RSI（Wilder 平滑）
    macd_dif    DIF / 收盘价（快慢线差，除以价格消掉量纲）
    macd_hist   (DIF - DEA) / 收盘价（MACD 柱，动量加速度）

事件因子（分组对照 + 逐期 t 检验）:
    gold_10     近 10 日内出现 MACD 金叉（柱由负转正）且当前柱 > 0
    dead_10     近 10 日内出现 MACD 死叉且当前柱 < 0
    bottom_div  底背离（简化口径）: 价格创 20 日前新低 而 DIF 抬高 且 DIF < 0
    top_div     顶背离（简化口径）: 价格创 20 日前新高 而 DIF 走低 且 DIF > 0

池口径与 validate_stock_factors.py 的 all 池一致：可交易 + 上市满 252 根 K 线，
前瞻收益 = 当期调仓日收盘到下期调仓日收盘（等比后复权）。

用法:
    python3 scripts/validate_technical_factors.py
"""
from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl
from scipy import stats

MIN_LISTED_BARS = 252
N_QUANTILES = 5


def load_technical_features(bars_path: Path) -> pl.DataFrame:
    """逐日逐股技术指标（全部只用当日及以前数据，EMA 按股票分区计算）。"""
    bars = pl.read_parquet(
        bars_path, columns=["datetime", "vt_symbol", "close"]
    ).with_columns(pl.col("datetime").cast(pl.Date).alias("d")).sort(["vt_symbol", "d"])

    over = {"partition_by": "vt_symbol"}
    # MACD 标准参数 12/26/9
    bars = bars.with_columns(
        (
            pl.col("close").ewm_mean(span=12).over(**over)
            - pl.col("close").ewm_mean(span=26).over(**over)
        ).alias("dif"),
    )
    bars = bars.with_columns(pl.col("dif").ewm_mean(span=9).over(**over).alias("dea"))
    bars = bars.with_columns((pl.col("dif") - pl.col("dea")).alias("hist"))

    # RSI14（Wilder 平滑 = ewm alpha=1/14）
    delta = pl.col("close") - pl.col("close").shift(1).over(**over)
    bars = bars.with_columns(
        delta.clip(lower_bound=0).ewm_mean(alpha=1 / 14).over(**over).alias("avg_gain"),
        (-delta).clip(lower_bound=0).ewm_mean(alpha=1 / 14).over(**over).alias("avg_loss"),
    )

    cross_up = (pl.col("hist") > 0) & (pl.col("hist").shift(1).over(**over) <= 0)
    cross_dn = (pl.col("hist") < 0) & (pl.col("hist").shift(1).over(**over) >= 0)

    return bars.with_columns(
        (100 - 100 / (1 + pl.col("avg_gain") / pl.col("avg_loss"))).alias("rsi_14"),
        (pl.col("dif") / pl.col("close")).alias("macd_dif"),
        (pl.col("hist") / pl.col("close")).alias("macd_hist"),
        (
            (cross_up.cast(pl.Int32).rolling_sum(10).over(**over) > 0) & (pl.col("hist") > 0)
        ).cast(pl.Int8).alias("gold_10"),
        (
            (cross_dn.cast(pl.Int32).rolling_sum(10).over(**over) > 0) & (pl.col("hist") < 0)
        ).cast(pl.Int8).alias("dead_10"),
        (
            (pl.col("close") < pl.col("close").shift(20).over(**over))
            & (pl.col("dif") > pl.col("dif").shift(20).over(**over))
            & (pl.col("dif") < 0)
        ).cast(pl.Int8).alias("bottom_div"),
        (
            (pl.col("close") > pl.col("close").shift(20).over(**over))
            & (pl.col("dif") < pl.col("dif").shift(20).over(**over))
            & (pl.col("dif") > 0)
        ).cast(pl.Int8).alias("top_div"),
        pl.int_range(pl.len()).over("vt_symbol").alias("listed_bars"),
    )


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


def build_snapshot(features: pl.DataFrame, universe: pl.DataFrame, period_ends: list) -> pl.DataFrame:
    """调仓日截面 + 下一持有期收益（口径与 validate_stock_factors.py 一致）。"""
    order = {d: i for i, d in enumerate(period_ends)}
    snap = (
        features.filter(pl.col("d").is_in(period_ends))
        .join(universe, left_on=["d", "vt_symbol"], right_on=["datetime", "vt_symbol"], how="left")
        .with_columns(
            pl.col("in_execution").fill_null(False),
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


def evaluate_continuous(pool: pl.DataFrame, factor: str, periods_per_year: int) -> dict:
    """连续因子：逐期 Rank IC + 5 分层（与 validate_stock_factors.py 相同口径）。"""
    data = pool.select("d", factor, "fwd_ret").drop_nulls()
    ics = []
    for (_, group) in data.group_by("d"):
        if len(group) >= 30:
            ic, _ = stats.spearmanr(group[factor].to_list(), group["fwd_ret"].to_list())
            ics.append(ic)
    s = pl.Series(ics)
    ic_mean, ic_std, n = s.mean(), s.std(), len(s)

    ranked = data.with_columns(
        ((pl.col(factor).rank("ordinal").over("d") - 1) * N_QUANTILES
         // pl.col(factor).count().over("d")).alias("q")
    )
    q_means = (
        ranked.group_by(["d", "q"]).agg(pl.col("fwd_ret").mean().alias("r"))
        .group_by("q").agg(pl.col("r").mean().alias("m")).sort("q").get_column("m").to_list()
    )
    top = ranked.filter(pl.col("q") == N_QUANTILES - 1).group_by("d").agg(pl.col("fwd_ret").mean().alias("t"))
    bot = ranked.filter(pl.col("q") == 0).group_by("d").agg(pl.col("fwd_ret").mean().alias("b"))
    spread = top.join(bot, on="d").with_columns((pl.col("t") - pl.col("b")).alias("sp")).get_column("sp")
    return {
        "ic_mean": ic_mean,
        "icir": ic_mean / ic_std if ic_std else float("nan"),
        "t_stat": ic_mean / ic_std * (n ** 0.5) if ic_std else float("nan"),
        "pct_positive": (s > 0).mean(),
        "q_means": q_means,
        "spread_ann": spread.mean() * periods_per_year,
        "spread_t": spread.mean() / spread.std() * (len(spread) ** 0.5) if spread.std() else float("nan"),
        "monotone": all(q_means[i] <= q_means[i + 1] for i in range(len(q_means) - 1)),
    }


def evaluate_event(pool: pl.DataFrame, flag: str, periods_per_year: int) -> dict:
    """事件因子：逐期比较"触发组 vs 未触发组"的平均前瞻收益，配对 t 检验。"""
    per_period = (
        pool.select("d", flag, "fwd_ret").drop_nulls()
        .group_by("d", flag).agg(pl.col("fwd_ret").mean().alias("r"), pl.len().alias("n"))
    )
    hit = per_period.filter(pl.col(flag) == 1).select("d", pl.col("r").alias("hit"), pl.col("n").alias("n_hit"))
    miss = per_period.filter(pl.col(flag) == 0).select("d", pl.col("r").alias("miss"))
    joined = hit.join(miss, on="d", how="inner").with_columns((pl.col("hit") - pl.col("miss")).alias("diff"))
    diff = joined.get_column("diff")
    n = len(diff)
    return {
        "hit_share": pool.get_column(flag).mean(),
        "n_periods": n,
        "hit_mean": joined.get_column("hit").mean(),
        "miss_mean": joined.get_column("miss").mean(),
        "diff_ann": (diff.mean() or 0.0) * periods_per_year,
        "diff_t": diff.mean() / diff.std() * (n ** 0.5) if n > 1 and diff.std() else float("nan"),
        "pct_positive": (diff > 0).mean(),
        "median_hits": hit.get_column("n_hit").median(),
    }


CONTINUOUS = ["rsi_14", "macd_dif", "macd_hist"]
EVENTS = ["gold_10", "dead_10", "bottom_div", "top_div"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", default="data/silver/daily_bars_adjusted.parquet")
    parser.add_argument("--universe", default="data/universe/execution_universe.parquet")
    parser.add_argument("--start", default="", help="只统计该日期之后的调仓期（子样本检验）")
    parser.add_argument("--label", default="technical")
    parser.add_argument("--output-dir", default="outputs/factor_checks")
    args = parser.parse_args()

    print("计算逐日技术指标 ...")
    features = load_technical_features(Path(args.bars))
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
    if args.start:
        trade_dates = [d for d in trade_dates if str(d) >= args.start]

    lines = [
        f"# 技术指标因子检验：{args.label}",
        "",
        f"- 样本区间: {trade_dates[0]} ~ {trade_dates[-1]}",
        f"- 池口径: 可交易 + 上市满 {MIN_LISTED_BARS} 根 K 线（全市场，与 none 模式候选池一致）",
        "- 前瞻收益 = 当期调仓日收盘到下期调仓日收盘（等比后复权）",
        "- 背离为简化口径（20 日价格/DIF 方向背离）；不含交易成本；全部为样本内统计",
        "",
    ]
    print("\n".join(lines))

    for freq, ppy in [("weekly", 52), ("monthly", 12)]:
        period_ends = build_period_ends(trade_dates, freq)
        snap = build_snapshot(features, universe, period_ends)
        pool_size = snap.group_by("d").len().get_column("len").median()
        lines.append(f"## {freq}（{len(period_ends)} 期，池中位 {pool_size:.0f} 只）")
        lines.append("")
        lines.append("### 连续因子（Rank IC + 5 分层）")
        lines.append("")
        lines.append("| 因子 | IC均值 | ICIR | t值 | IC>0 | Q1(低) | Q3 | Q5(高) | 顶底价差年化 | 价差t | 单调 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for factor in CONTINUOUS:
            r = evaluate_continuous(snap, factor, ppy)
            q = r["q_means"]
            row = (
                f"| {factor} | {r['ic_mean']:+.3f} | {r['icir']:+.2f} | {r['t_stat']:+.2f} "
                f"| {r['pct_positive']:.0%} | {q[0]*100:+.2f}% | {q[2]*100:+.2f}% | {q[-1]*100:+.2f}% "
                f"| {r['spread_ann']*100:+.1f}% | {r['spread_t']:+.2f} | {'是' if r['monotone'] else '否'} |"
            )
            lines.append(row)
            print(row)
        lines.append("")
        lines.append("### 事件因子（触发组 vs 未触发组，逐期配对）")
        lines.append("")
        lines.append("| 事件 | 触发占比 | 期数 | 触发组均值 | 未触发组均值 | 差值年化 | 差值t | 差值>0期占比 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for flag in EVENTS:
            r = evaluate_event(snap, flag, ppy)
            row = (
                f"| {flag} | {r['hit_share']:.1%} | {r['n_periods']} | {r['hit_mean']*100:+.2f}% "
                f"| {r['miss_mean']*100:+.2f}% | {r['diff_ann']*100:+.1f}% | {r['diff_t']:+.2f} "
                f"| {r['pct_positive']:.0%} |"
            )
            lines.append(row)
            print(row)
        lines.append("")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"technical_factors_{args.label}.md"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已写入 {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
