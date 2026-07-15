"""PB 反价值因子检验（用户测试用）。

PB = close / 每股净资产。传统价值因子用低 PB 加分；用户提问"PB 越高越买"
= 反价值因子（预期负 IC）。假设：反转策略容易挑到"低 PB 破位股（价值陷阱）"，
加 PB 高加分可能过滤掉这些陷阱。

数据源：
- close: data/silver/daily_bars_adjusted.parquet（后复权价）
- 每股净资产: data/akshare/financial_indicators.parquet 里的 "每股净资产_调整后(元)"
- PIT 对齐：indicator 表无公告日，与 reports 表按 report_date join 取 announce_date
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import polars as pl
from scipy import stats

MIN_LISTED_BARS = 252
N_QUANTILES = 5
VOL_CAP = 3.0


def load_pb_pit(reports_path: Path, indicators_path: Path) -> pl.DataFrame:
    """构造每股净资产的 PIT 表：(vt_symbol, announce_date, bvps)。

    每股净资产在 indicators 表，公告日在 reports 表。按 (vt_symbol, report_date) join。
    """
    reports = pl.read_parquet(reports_path)
    indicators = pl.read_parquet(indicators_path)

    # 从 reports 抽 announce_date（利润表就够，三表 announce_date 一致）
    ann = (
        reports.filter(pl.col("report_type") == "利润表")
        .select("vt_symbol", "report_date", "announce_date")
        .unique(subset=["vt_symbol", "report_date"])
    )

    # 从 indicators 抽每股净资产
    bvps_records = []
    for r in indicators.iter_rows(named=True):
        p = json.loads(r["payload_json"])
        v = p.get("每股净资产_调整后(元)")
        try:
            v = float(v) if v not in (None, "--", "") else None
        except (ValueError, TypeError):
            v = None
        bvps_records.append(v)
    ind = indicators.select("vt_symbol", "report_date").with_columns(
        pl.Series("bvps", bvps_records, dtype=pl.Float64)
    ).with_columns(
        # indicator 的 report_date 是 'YYYY-MM-DD'，reports 是 'YYYYMMDD'，统一
        pl.col("report_date").str.replace_all("-", "").alias("report_date")
    )

    # join 拿到公告日
    merged = ind.join(ann, on=["vt_symbol", "report_date"], how="left").filter(
        pl.col("announce_date").is_not_null() & pl.col("bvps").is_not_null() & (pl.col("bvps") > 0)
    )
    merged = merged.with_columns(
        pl.col("announce_date").str.strptime(pl.Date, "%Y%m%d", strict=False)
    ).drop_nulls("announce_date").sort(["vt_symbol", "announce_date"])

    return merged.select("vt_symbol", "announce_date", "bvps")


def load_stock_features(bars_path: Path) -> pl.DataFrame:
    bars = pl.read_parquet(
        bars_path, columns=["datetime", "vt_symbol", "close", "high", "turnover"]
    ).with_columns(pl.col("datetime").cast(pl.Date).alias("d")).sort(["vt_symbol", "d"])

    over = {"partition_by": "vt_symbol"}
    return bars.with_columns(
        (pl.col("close") / pl.col("close").shift(20).over(**over) - 1).alias("ret_20"),
        (pl.col("close") / pl.col("close").shift(60).over(**over) - 1).alias("ret_60"),
        (pl.col("close") / pl.col("high").rolling_max(252).over(**over)).alias("nh_252"),
        (
            pl.col("turnover").rolling_mean(20).over(**over)
            / pl.col("turnover").rolling_mean(60).over(**over)
        ).clip(upper_bound=VOL_CAP).alias("vol_ratio"),
        pl.int_range(pl.len()).over("vt_symbol").alias("listed_bars"),
    )


def load_industry_momentum(bars, members_path, prefix):
    members = (
        pl.read_parquet(members_path)
        .filter(pl.col("sector").str.starts_with(prefix))
        .select(pl.col("sector").alias("industry"), "vt_symbol")
        .unique()
    )
    daily = (
        bars.select("vt_symbol", "d", "close").sort(["vt_symbol", "d"])
        .with_columns((pl.col("close") / pl.col("close").shift(1).over("vt_symbol") - 1).alias("ret"))
        .drop_nulls("ret")
        .join(members, on="vt_symbol", how="inner")
        .group_by(["industry", "d"]).agg(pl.col("ret").mean().alias("ind_ret"))
        .sort(["industry", "d"])
        .with_columns((pl.col("ind_ret") + 1).cum_prod().over("industry").alias("idx"))
    )
    momentum = daily.with_columns(
        (pl.col("idx") / pl.col("idx").shift(20).over("industry") - 1).alias("ind_mom_20"),
        (pl.col("idx") / pl.col("idx").shift(60).over("industry") - 1).alias("ind_mom_60"),
    )
    return momentum.select("industry", "d", "ind_mom_20", "ind_mom_60"), members


def build_period_ends(trade_dates, freq):
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


def evaluate_factor(pool, factor, ppy):
    data = pool.select("d", factor, "fwd_ret").drop_nulls()
    ics = []
    for (_, g) in data.group_by("d"):
        if len(g) >= 30:
            ic, _ = stats.spearmanr(g[factor].to_list(), g["fwd_ret"].to_list())
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
        "n": n,
        "ic": ic_mean, "icir": ic_mean / ic_std if ic_std else float('nan'),
        "t": ic_mean / ic_std * (n ** 0.5) if ic_std else float('nan'),
        "pct_pos": (s > 0).mean(),
        "q_means": q_means,
        "spread_ann": spread.mean() * ppy,
        "spread_t": spread.mean() / spread.std() * (len(spread) ** 0.5) if spread.std() else float('nan'),
        "monotone": "up" if monotone_up else ("down" if monotone_dn else "no"),
    }


def cross_section_rank_corr(pool, a, b):
    data = pool.select("d", a, b).drop_nulls()
    corrs = []
    for (_, g) in data.group_by("d"):
        if len(g) >= 30:
            r, _ = stats.spearmanr(g[a].to_list(), g[b].to_list())
            corrs.append(r)
    return float(pl.Series(corrs).mean()) if corrs else float('nan')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", default="data/silver/daily_bars_adjusted.parquet")
    parser.add_argument("--reports", default="data/akshare/financial_reports.parquet")
    parser.add_argument("--indicators", default="data/akshare/financial_indicators.parquet")
    parser.add_argument("--universe", default="data/universe/execution_universe.parquet")
    parser.add_argument("--label", default="gics1")
    parser.add_argument("--output-dir", default="outputs/factor_checks")
    args = parser.parse_args()

    print("加载 T6 财务数据 ...")
    pb_pit = load_pb_pit(Path(args.reports), Path(args.indicators))
    print(f"PB PIT: {pb_pit.height} 条记录, {pb_pit.get_column('vt_symbol').unique().len()} 只")

    features = load_stock_features(Path(args.bars))
    industry_momentum, members = load_industry_momentum(
        features, Path("data/sector/sector_members.parquet"), "GICS1"
    )
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
        f"# PB 反价值因子检验：{args.label}",
        "",
        "- 定义：pb = close / 每股净资产（后复权价 × BVPS）",
        "- 传统价值因子：低 PB 加分（预期正 IC）",
        "- 反价值：**PB 越高越买**（预期负 IC）",
        "- 假设：反转策略挑到很多低 PB 破位股（价值陷阱），加 PB 反向可能过滤",
        f"- 样本区间: {trade_dates[0]} ~ {trade_dates[-1]}",
        "- PIT 对齐：announce_date < 调仓日的最新每股净资产",
        "",
    ]

    corr_snapshots = {}

    for freq, rs_col, ppy in [("monthly", "rs_60", 12), ("weekly", "rs_20", 52)]:
        period_ends = build_period_ends(trade_dates, freq)
        order = {d: i for i, d in enumerate(period_ends)}

        # 价量截面
        px_snap = (
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

        # PIT 对齐 PB
        px_sorted = px_snap.sort(["vt_symbol", "d"])
        pb_sorted = pb_pit.sort(["vt_symbol", "announce_date"])
        combined = px_sorted.join_asof(
            pb_sorted, left_on="d", right_on="announce_date",
            by="vt_symbol", strategy="backward",
        )

        # 算 PB
        combined = combined.with_columns(
            (pl.col("close") / pl.col("bvps")).alias("pb")
        )

        pool = combined.filter(
            pl.col("fwd_ret").is_not_null()
            & pl.col("in_execution")
            & (pl.col("listed_bars") >= MIN_LISTED_BARS)
            & pl.col("pb").is_not_null()
            & (pl.col("pb") > 0)   # 剔除负资产
        )
        pool_size = pool.group_by("d").len().get_column("len").median() if pool.height else 0

        r = evaluate_factor(pool, "pb", ppy)
        q = r["q_means"]
        mono = {"up": "升", "down": "降", "no": "否"}[r["monotone"]]
        row = (f"| pb | {r['ic']:+.3f} | {r['icir']:+.2f} | {r['t']:+.2f} "
               f"| {r['pct_pos']:.0%} | {q[0]*100:+.2f}% | {q[2]*100:+.2f}% | {q[-1]*100:+.2f}% "
               f"| {r['spread_ann']*100:+.1f}% | {r['spread_t']:+.2f} | {mono} |")

        lines.append(f"## {freq} / all 池（{len(period_ends)} 期，池中位 {pool_size:.0f} 只）")
        lines.append("")
        lines.append("| 因子 | IC均值 | ICIR | t值 | IC>0 | Q1 | Q3 | Q5 | 顶底价差年化 | 价差t | 单调 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        lines.append(row)
        print(row)
        lines.append("")
        corr_snapshots[freq] = pool.select("d", "pb", rs_col, "nh_252", "vol_ratio")

    # 独立性
    lines.append("## 与生产三因子的截面秩相关（均值）")
    lines.append("")
    lines.append("| 频率 | 与 rs | 与 nh | 与 vol |")
    lines.append("|---|---|---|---|")
    for freq, pool in corr_snapshots.items():
        rs_col = "rs_20" if freq == "weekly" else "rs_60"
        r_rs = cross_section_rank_corr(pool, "pb", rs_col)
        r_nh = cross_section_rank_corr(pool, "pb", "nh_252")
        r_vol = cross_section_rank_corr(pool, "pb", "vol_ratio")
        lines.append(f"| {freq} | {r_rs:+.2f} | {r_nh:+.2f} | {r_vol:+.2f} |")
    lines.append("")

    lines.append("## 闸门")
    lines.append("- 通过条件: |t| >= 2 且 与三因子相关 |ρ| < 0.5")
    lines.append("- 通过则接入 stocks.py 打分（用正权重，PB 越高分越高）")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"pb_factor_{args.label}.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告写入 {out_dir}/pb_factor_{args.label}.md")


if __name__ == "__main__":
    main()
