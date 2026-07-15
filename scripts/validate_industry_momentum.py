"""行业动量因子的正式检验：Rank IC + 分层回测。

回答一个问题：\"行业过去 W 日动量\" 对 \"行业下一持有期收益\" 有没有预测力。
支持不同行业口径（GICS1 / 申万一级）、不同持有期（周 / 月）、多个动量窗口。

方法:
1. 行业日收益 = 行业内成分股（等比后复权）日收益的等权平均
2. 动量因子 = 行业累计净值 idx[t] / idx[t-W] - 1（信号日 t 收盘可得, 无未来信息）
3. 前瞻收益 = idx[下一调仓日] / idx[t] - 1
4. Rank IC = 每期 spearman(动量, 前瞻收益)，报告均值 / ICIR / t 值 / 正比例
5. 分层回测 = 每期按动量排序分 N 层，各层等权持有到下期，看单调性和多空价差

用法:
    python3 scripts/validate_industry_momentum.py --members data/sector/sector_members.parquet --prefix GICS1 --label gics1
    python3 scripts/validate_industry_momentum.py --members data/sector/sw1_members.parquet --prefix SW1 --label sw1
"""
from __future__ import annotations

import argparse
from pathlib import Path

import polars as pl
from scipy import stats


MIN_MEMBERS = 5  # 成分股太少的行业动量不可靠，与信号层口径一致


def load_industry_index(bars_path: Path, members_path: Path, prefix: str) -> pl.DataFrame:
    """行业等权日收益 → 行业累计净值。返回列: industry / d / idx / n_members。"""
    bars = pl.read_parquet(bars_path, columns=["datetime", "vt_symbol", "close"]).with_columns(
        pl.col("datetime").cast(pl.Date).alias("d")
    )
    members = (
        pl.read_parquet(members_path)
        .filter(pl.col("sector").str.starts_with(prefix))
        .select(pl.col("sector").alias("industry"), "vt_symbol")
        .unique()
    )
    returns = (
        bars.sort(["vt_symbol", "d"])
        .with_columns((pl.col("close") / pl.col("close").shift(1).over("vt_symbol") - 1).alias("ret"))
        .drop_nulls("ret")
        .join(members, on="vt_symbol", how="inner")
    )
    daily = (
        returns.group_by(["industry", "d"])
        .agg(pl.col("ret").mean().alias("ind_ret"), pl.len().alias("n_members"))
        .sort(["industry", "d"])
        .with_columns((pl.col("ind_ret") + 1).cum_prod().over("industry").alias("idx"))
    )
    return daily


def build_period_ends(trading_dates: list, freq: str) -> list:
    """每周 / 每月最后一个交易日（最后一个不完整周期丢弃）。"""
    frame = pl.DataFrame({"d": trading_dates}).sort("d")
    if freq == "weekly":
        key = pl.col("d").dt.iso_year().cast(pl.String) + "-W" + pl.col("d").dt.week().cast(pl.String)
    else:
        key = pl.col("d").dt.strftime("%Y-%m")
    ends = (
        frame.with_columns(key.alias("k"))
        .group_by("k")
        .agg(pl.col("d").max())
        .sort("d")
        .get_column("d")
        .to_list()
    )
    return ends[:-1]  # 最后一个周期可能没走完，丢弃


def factor_table(daily: pl.DataFrame, period_ends: list, window: int) -> pl.DataFrame:
    """在每个调仓日截面上计算: 动量因子 + 下一持有期收益。"""
    snap = daily.filter(pl.col("d").is_in(period_ends))
    snap = snap.sort(["industry", "d"]).with_columns(
        pl.col("idx").alias("idx_now"),
        (pl.col("idx").shift(-1).over("industry") / pl.col("idx") - 1).alias("fwd_ret"),
    )
    # 动量窗口按交易日回看：把日频净值向后 shift window 天再对齐到调仓日
    lag = daily.sort(["industry", "d"]).with_columns(
        pl.col("idx").shift(window).over("industry").alias("idx_lag")
    ).select("industry", "d", "idx_lag")
    snap = snap.join(lag, on=["industry", "d"], how="left").with_columns(
        (pl.col("idx_now") / pl.col("idx_lag") - 1).alias("momentum")
    )
    return snap.filter(
        pl.col("momentum").is_not_null()
        & pl.col("fwd_ret").is_not_null()
        & (pl.col("n_members") >= MIN_MEMBERS)
    ).select("d", "industry", "momentum", "fwd_ret")


def rank_ic_stats(table: pl.DataFrame) -> dict:
    ics = []
    for (_, group) in table.group_by("d"):
        if len(group) >= 5:
            ic, _ = stats.spearmanr(group["momentum"].to_list(), group["fwd_ret"].to_list())
            ics.append(ic)
    s = pl.Series(ics)
    n = len(s)
    mean, std = s.mean(), s.std()
    return {
        "n_periods": n,
        "ic_mean": mean,
        "icir": mean / std if std else float("nan"),
        "t_stat": mean / std * (n ** 0.5) if std else float("nan"),
        "pct_positive": (s > 0).mean(),
    }


def quantile_backtest(table: pl.DataFrame, n_quantiles: int) -> pl.DataFrame:
    """每期按动量分层，各层等权持有一期。返回各层的年化收益与多空价差。"""
    ranked = table.with_columns(
        ((pl.col("momentum").rank("ordinal").over("d") - 1)
         * n_quantiles // pl.col("momentum").count().over("d")).alias("q")
    )
    per_period = (
        ranked.group_by(["d", "q"]).agg(pl.col("fwd_ret").mean().alias("q_ret")).sort(["q", "d"])
    )
    summary = (
        per_period.group_by("q")
        .agg(
            pl.col("q_ret").mean().alias("mean_ret"),
            pl.col("q_ret").std().alias("std_ret"),
            pl.len().alias("n"),
        )
        .sort("q")
        .with_columns((pl.col("mean_ret") / pl.col("std_ret") * pl.col("n").sqrt()).alias("t_stat"))
    )
    return summary


def spread_stats(table: pl.DataFrame, n_quantiles: int) -> dict:
    """顶层减底层的逐期价差序列及显著性。"""
    ranked = table.with_columns(
        ((pl.col("momentum").rank("ordinal").over("d") - 1)
         * n_quantiles // pl.col("momentum").count().over("d")).alias("q")
    )
    top = ranked.filter(pl.col("q") == n_quantiles - 1).group_by("d").agg(pl.col("fwd_ret").mean().alias("top"))
    bot = ranked.filter(pl.col("q") == 0).group_by("d").agg(pl.col("fwd_ret").mean().alias("bot"))
    joined = top.join(bot, on="d", how="inner").with_columns((pl.col("top") - pl.col("bot")).alias("spread"))
    s = joined.get_column("spread")
    n = len(s)
    return {
        "spread_mean": s.mean(),
        "t_stat": s.mean() / s.std() * (n ** 0.5) if s.std() else float("nan"),
        "n": n,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", default="data/silver/daily_bars_adjusted.parquet")
    parser.add_argument("--members", default="data/sector/sector_members.parquet")
    parser.add_argument("--prefix", default="GICS1")
    parser.add_argument("--label", default="gics1")
    parser.add_argument("--windows", default="5,10,20,40,60")
    parser.add_argument("--start", default=None, help="样本起始日 YYYY-MM-DD（用于子区间检验）")
    parser.add_argument("--output-dir", default="outputs/factor_checks")
    args = parser.parse_args()

    daily = load_industry_index(Path(args.bars), Path(args.members), args.prefix)
    if args.start:
        from datetime import date
        daily = daily.filter(pl.col("d") >= date.fromisoformat(args.start))
    trading_dates = sorted(daily.get_column("d").unique().to_list())
    windows = [int(w) for w in args.windows.split(",")]
    n_industries = daily.get_column("industry").n_unique()
    # GICS1 只有 11 个行业分 3 层，申万 31 个分 5 层
    n_quantiles = 5 if n_industries >= 20 else 3

    lines = [
        f"# 行业动量因子检验：{args.label}",
        "",
        f"- 行业口径: {args.prefix}（{n_industries} 个行业，分 {n_quantiles} 层）",
        f"- 样本区间: {trading_dates[0]} ~ {trading_dates[-1]}",
        f"- 行情: 等比后复权收盘价；行业收益 = 成分股等权；成分 >= {MIN_MEMBERS} 只才参与",
        "- 局限: 行业成分为静态快照（有分类漂移偏差）；无交易成本；全部为样本内统计",
        "",
    ]
    print("\n".join(lines))

    for freq in ["weekly", "monthly"]:
        period_ends = build_period_ends(trading_dates, freq)
        lines.append(f"## 持有期: {freq}（{len(period_ends)} 期）")
        lines.append("")
        header = "| 动量窗口 | IC均值 | ICIR | IC t值 | IC>0比例 | 顶层年均超额 | 顶底价差/期 | 价差t值 | 分层单调性 |"
        lines.append(header)
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for window in windows:
            table = factor_table(daily, period_ends, window)
            ic = rank_ic_stats(table)
            quant = quantile_backtest(table, n_quantiles)
            spread = spread_stats(table, n_quantiles)
            rets = quant.get_column("mean_ret").to_list()
            monotone = "是" if all(rets[i] <= rets[i + 1] for i in range(len(rets) - 1)) else "否"
            periods_per_year = 52 if freq == "weekly" else 12
            top_ann = rets[-1] * periods_per_year
            avg_ann = (sum(rets) / len(rets)) * periods_per_year
            row = (
                f"| {window}日 | {ic['ic_mean']:+.3f} | {ic['icir']:+.2f} | {ic['t_stat']:+.2f} "
                f"| {ic['pct_positive']:.0%} | {(top_ann - avg_ann) * 100:+.1f}% "
                f"| {spread['spread_mean'] * 100:+.2f}% | {spread['t_stat']:+.2f} | {monotone} |"
            )
            lines.append(row)
            print(row)
        lines.append("")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"industry_momentum_{args.label}.md"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已写入 {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
