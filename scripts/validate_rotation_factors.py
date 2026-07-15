"""行业轮转识别因子的正式检验：排名爬升速度 / 成交额占比变化 / 新高广度（009 后续）。

背景：008 已证明行业动量*水平*无横截面预测力、行业层被移除（industry_mode=none）。
本脚本检验的是"变化率型"轮转因子——它们与水平型动量是不同的信息源，
用户希望验证后再决定是否重建行业层。

因子（行业级截面，默认申万 31 行业）：
    ind_mom_60     行业 60 日等权动量（水平型，历史已证无效，放这里当基线对照）
    rank_velocity  动量排名爬升速度 = 当日动量百分位 - 20 个交易日前动量百分位，
                   正数 = 排名在爬升（"从榜单中后段爬进前列"的量化版）
    vol_share_chg  成交额占比变化 = 行业成交额占全市场比的 20 日均 / 60 日均，
                   > 1 = 资金正在向该行业聚集
    nh_breadth     新高广度 = 行业内 收盘 >= 252 日最高 95% 的成分股占比

前瞻收益 = 行业等权指数当期调仓日到下期调仓日的收益。
行业数少（31 个），分层用 3 层；同时报告各因子与动量水平的截面相关（正交性）。

用法:
    python3 scripts/validate_rotation_factors.py
    python3 scripts/validate_rotation_factors.py --industry-source gics1 --label gics1
"""
from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl
from scipy import stats

MIN_MEMBERS = 5       # 行业当日有效成分股下限
MIN_INDUSTRIES = 10   # 截面参与统计的行业数下限
N_QUANTILES = 3       # 行业数少，用 3 分层
RANK_VELOCITY_LAG = 20
NH_THRESHOLD = 0.95


def build_industry_daily(bars_path: Path, members_path: Path, prefix: str) -> pl.DataFrame:
    """逐日行业聚合表: industry, d, ind_ret, ind_mom_60, vol_share_chg, nh_breadth。"""
    members = (
        pl.read_parquet(members_path)
        .filter(pl.col("sector").str.starts_with(prefix))
        .select(pl.col("sector").alias("industry"), "vt_symbol")
        .unique()
    )
    bars = (
        pl.read_parquet(bars_path, columns=["datetime", "vt_symbol", "close", "high", "turnover"])
        .with_columns(pl.col("datetime").cast(pl.Date).alias("d"))
        .sort(["vt_symbol", "d"])
    )
    over = {"partition_by": "vt_symbol"}
    stock_daily = bars.with_columns(
        (pl.col("close") / pl.col("close").shift(1).over(**over) - 1).alias("ret"),
        (pl.col("close") >= NH_THRESHOLD * pl.col("high").rolling_max(252).over(**over)).alias("near_high"),
    ).join(members, on="vt_symbol", how="inner")

    market_turnover = stock_daily.group_by("d").agg(pl.col("turnover").sum().alias("mkt_turnover"))

    daily = (
        stock_daily.group_by(["industry", "d"])
        .agg(
            pl.col("ret").mean().alias("ind_ret"),
            pl.col("turnover").sum().alias("ind_turnover"),
            pl.col("near_high").mean().alias("nh_breadth"),
            pl.len().alias("n_members"),
        )
        .filter(pl.col("n_members") >= MIN_MEMBERS)
        .join(market_turnover, on="d", how="left")
        .sort(["industry", "d"])
    )
    iover = {"partition_by": "industry"}
    daily = daily.with_columns(
        (pl.col("ind_ret").fill_null(0.0) + 1).cum_prod().over(**iover).alias("idx"),
        (pl.col("ind_turnover") / pl.col("mkt_turnover")).alias("vol_share"),
    )
    daily = daily.with_columns(
        (pl.col("idx") / pl.col("idx").shift(60).over(**iover) - 1).alias("ind_mom_60"),
        (
            pl.col("vol_share").rolling_mean(20).over(**iover)
            / pl.col("vol_share").rolling_mean(60).over(**iover)
        ).alias("vol_share_chg"),
    )
    # 排名爬升速度：动量的当日截面百分位 - RANK_VELOCITY_LAG 日前的百分位
    daily = daily.with_columns(
        (pl.col("ind_mom_60").rank("average").over("d") / pl.col("ind_mom_60").count().over("d"))
        .alias("mom_rank_pct")
    ).with_columns(
        (pl.col("mom_rank_pct") - pl.col("mom_rank_pct").shift(RANK_VELOCITY_LAG).over(**iover))
        .alias("rank_velocity")
    )
    return daily


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


def build_snapshot(daily: pl.DataFrame, period_ends: list) -> pl.DataFrame:
    """调仓日行业截面 + 下一持有期行业收益。"""
    order = {d: i for i, d in enumerate(period_ends)}
    snap = (
        daily.filter(pl.col("d").is_in(period_ends))
        .with_columns(pl.col("d").replace_strict(order, return_dtype=pl.Int64).alias("pidx"))
        .sort(["industry", "pidx"])
        .with_columns(
            pl.when(pl.col("pidx").shift(-1).over("industry") == pl.col("pidx") + 1)
            .then(pl.col("idx").shift(-1).over("industry") / pl.col("idx") - 1)
            .otherwise(None)
            .alias("fwd_ret")
        )
    )
    return snap.filter(pl.col("fwd_ret").is_not_null())


def evaluate(snap: pl.DataFrame, factor: str, periods_per_year: int) -> dict:
    """逐期 Rank IC + 3 分层（截面单位是行业）。"""
    data = snap.select("d", factor, "fwd_ret").drop_nulls()
    ics = []
    for (_, group) in data.group_by("d"):
        if len(group) >= MIN_INDUSTRIES:
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
        "n_periods": n,
        "ic_mean": ic_mean,
        "icir": ic_mean / ic_std if ic_std else float("nan"),
        "t_stat": ic_mean / ic_std * (n ** 0.5) if ic_std else float("nan"),
        "pct_positive": (s > 0).mean(),
        "q_means": q_means,
        "spread_ann": (spread.mean() or 0.0) * periods_per_year,
        "spread_t": spread.mean() / spread.std() * (len(spread) ** 0.5) if spread.std() else float("nan"),
    }


def cross_corr(snap: pl.DataFrame, factor_a: str, factor_b: str) -> float:
    """两因子的逐期截面秩相关均值（正交性检查）。"""
    cors = []
    for (_, group) in snap.select("d", factor_a, factor_b).drop_nulls().group_by("d"):
        if len(group) >= MIN_INDUSTRIES:
            c, _ = stats.spearmanr(group[factor_a].to_list(), group[factor_b].to_list())
            cors.append(c)
    return float(pl.Series(cors).mean()) if cors else float("nan")


FACTORS = ["ind_mom_60", "rank_velocity", "vol_share_chg", "nh_breadth"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", default="data/silver/daily_bars_adjusted.parquet")
    parser.add_argument("--universe", default="data/universe/execution_universe.parquet")
    parser.add_argument("--industry-source", default="sw1", choices=["gics1", "sw1"])
    parser.add_argument("--label", default="sw1")
    parser.add_argument("--output-dir", default="outputs/factor_checks")
    args = parser.parse_args()

    if args.industry_source == "sw1":
        members_path, prefix = Path("data/sector/sw1_members.parquet"), "SW1"
    else:
        members_path, prefix = Path("data/sector/sector_members.parquet"), "GICS1"

    print("聚合逐日行业指标 ...")
    daily = build_industry_daily(Path(args.bars), members_path, prefix)

    universe = pl.read_parquet(args.universe, columns=["datetime"]).with_columns(
        pl.col("datetime").cast(pl.Date)
    )
    dmin, dmax = universe.get_column("datetime").min(), universe.get_column("datetime").max()
    trade_dates = sorted(
        daily.filter((pl.col("d") >= dmin) & (pl.col("d") <= dmax))
        .get_column("d").unique().to_list()
    )

    lines = [
        f"# 行业轮转因子检验：{args.label}",
        "",
        f"- 行业口径: {prefix}，行业当日成分 >= {MIN_MEMBERS} 只才入截面",
        f"- 样本区间: {trade_dates[0]} ~ {trade_dates[-1]}",
        f"- 前瞻收益 = 行业等权指数当期调仓日到下期调仓日收益；{N_QUANTILES} 分层",
        f"- rank_velocity 回看 {RANK_VELOCITY_LAG} 个交易日；nh_breadth 阈值 {NH_THRESHOLD:.0%}",
        "- 行业成分为静态快照；不含交易成本；全部为样本内统计",
        "",
    ]
    print("\n".join(lines))

    for freq, ppy in [("weekly", 52), ("monthly", 12)]:
        period_ends = build_period_ends(trade_dates, freq)
        snap = build_snapshot(daily.filter(pl.col("d") >= trade_dates[0]), period_ends)
        n_ind = snap.group_by("d").len().get_column("len").median()
        lines.append(f"## {freq}（{len(period_ends)} 期，截面中位 {n_ind:.0f} 个行业）")
        lines.append("")
        lines.append("| 因子 | IC均值 | ICIR | t值 | IC>0 | Q1(低) | Q2 | Q3(高) | 顶底价差年化 | 价差t |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for factor in FACTORS:
            r = evaluate(snap, factor, ppy)
            q = r["q_means"]
            row = (
                f"| {factor} | {r['ic_mean']:+.3f} | {r['icir']:+.2f} | {r['t_stat']:+.2f} "
                f"| {r['pct_positive']:.0%} | {q[0]*100:+.2f}% | {q[1]*100:+.2f}% | {q[-1]*100:+.2f}% "
                f"| {r['spread_ann']*100:+.1f}% | {r['spread_t']:+.2f} |"
            )
            lines.append(row)
            print(f"[{freq}] {row}")
        lines.append("")
        lines.append("与动量水平的截面秩相关（正交性）：")
        for factor in ["rank_velocity", "vol_share_chg", "nh_breadth"]:
            c = cross_corr(snap, factor, "ind_mom_60")
            lines.append(f"- {factor} vs ind_mom_60: {c:+.2f}")
            print(f"[{freq}] {factor} vs ind_mom_60: {c:+.2f}")
        lines.append("")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"rotation_factors_{args.label}.md"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已写入 {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
