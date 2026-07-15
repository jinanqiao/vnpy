"""基本面因子检验（T6）：SUE + ROE + 净利润同比 + 应计。

数据源：
- data/akshare/financial_reports.parquet  三张表（含公告日 announce_date）
- data/akshare/financial_indicators.parquet  86 个派生指标（无公告日，跟三表 join）

四个候选因子：
    SUE   标准化未预期盈利 = (本季净利润 - 过去 4 季均值) / 过去 4 季 std
          预期正 IC，盈利超预期 → 下期跑赢
    ROE   净资产收益率（indicator 表 "净资产收益率(%)"），
          用最近一期已公告的年化 ROE。预期正 IC（质量因子）
    NI_YoY 净利润同比 = 本季净利润 / 去年同期 - 1
          预期正 IC（成长因子）
    ACC   应计项异常（Sloan） = (净利润 - 经营性现金流) / 平均总资产
          预期负 IC（低应计 = 真实盈利质量高）

PIT 对齐：
    每个调仓日只用"announce_date < 调仓日"的最新一期数据。
    indicator 表本身无 announce_date，与 reports 表按 (vt_symbol, report_date)
    left-join 三表的公告日；如没匹配则用"保守滞后两季"策略（announce_date =
    report_date + 90 天）。

方法（与 011/013 检验模板一致）：
    Rank IC + 5 分层 + 与生产三因子 rs/nh/vol_ratio 的截面相关
闸门：|t| >= 2 且相关 |ρ| < 0.5

用法:
    python3 scripts/validate_fundamental_factors.py
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import polars as pl
from scipy import stats

MIN_LISTED_BARS = 252
N_QUANTILES = 5
VOL_CAP = 3.0


def load_reports_field(reports: pl.DataFrame, field: str, report_type: str) -> pl.DataFrame:
    """从 reports.payload_json 里抽单一字段，返回 (vt_symbol, report_date, announce_date, value)。"""
    filtered = reports.filter(pl.col("report_type") == report_type)
    values = []
    for r in filtered.iter_rows(named=True):
        payload = json.loads(r["payload_json"])
        v = payload.get(field)
        try:
            v = float(v) if v is not None else None
        except (ValueError, TypeError):
            v = None
        values.append(v)
    return filtered.select("vt_symbol", "report_date", "announce_date").with_columns(
        pl.Series(field, values, dtype=pl.Float64)
    )


def load_indicators_fields(indicators: pl.DataFrame, fields: list[str]) -> pl.DataFrame:
    """从 indicators.payload_json 里抽多个字段。"""
    cols = {f: [] for f in fields}
    for r in indicators.iter_rows(named=True):
        payload = json.loads(r["payload_json"])
        for f in fields:
            v = payload.get(f)
            try:
                v = float(v) if v not in (None, "--", "") else None
            except (ValueError, TypeError):
                v = None
            cols[f].append(v)
    return indicators.select("vt_symbol", "report_date").with_columns(
        [pl.Series(f, cols[f], dtype=pl.Float64) for f in fields]
    )


def parse_report_date(col: str) -> pl.Expr:
    """把 'YYYYMMDD' 字符串转成 Date。"""
    return pl.col(col).str.strptime(pl.Date, "%Y%m%d", strict=False)


def build_factor_snapshot(
    features: pl.DataFrame,
    factor_df: pl.DataFrame,
    period_ends: list,
    ret_days_back: int,
) -> pl.DataFrame:
    """把 PIT 因子拼到调仓日截面。

    factor_df schema: (vt_symbol, announce_date, factor_value)
    每个调仓日 t，选每只股票 announce_date < t 的最新一条 factor_value。
    """
    order = {d: i for i, d in enumerate(period_ends)}
    # 用 asof join 做 PIT 对齐
    ann = factor_df.sort(["vt_symbol", "announce_date"]).with_columns(
        pl.col("announce_date").alias("d_ann")
    )

    # 每个调仓日 x vt_symbol，找 announce_date <= d - 1 天的最新记录
    period_frame = pl.DataFrame({"d": period_ends}).with_columns(pl.col("d").cast(pl.Date))
    snap_dates = period_frame.join(
        features.select("vt_symbol").unique(), how="cross"
    ).with_columns(pl.col("d").cast(pl.Date))

    # asof join（backward）：找到 announce_date < d 的最大 announce_date
    joined = snap_dates.sort(["vt_symbol", "d"]).join_asof(
        ann.select("vt_symbol", "d_ann", pl.col("d_ann").alias("announce_date"), *[c for c in ann.columns if c not in ("vt_symbol", "d_ann", "announce_date", "report_date")]).sort(["vt_symbol", "d_ann"]),
        left_on="d", right_on="d_ann", by="vt_symbol", strategy="backward",
    )
    return joined


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


def evaluate_factor(pool: pl.DataFrame, factor: str, periods_per_year: int) -> dict:
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
        "n_periods": n,
        "ic_mean": ic_mean,
        "icir": ic_mean / ic_std if ic_std else float("nan"),
        "t_stat": ic_mean / ic_std * (n ** 0.5) if ic_std else float("nan"),
        "pct_positive": (s > 0).mean(),
        "q_means": q_means,
        "spread_ann": spread.mean() * periods_per_year,
        "spread_t": spread.mean() / spread.std() * (len(spread) ** 0.5) if spread.std() else float("nan"),
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


def build_pit_factor(reports: pl.DataFrame, indicators: pl.DataFrame) -> pl.DataFrame:
    """构造四个基本面因子 + 公告日的宽表。

    每行 (vt_symbol, announce_date, sue, roe, ni_yoy, acc)：一个公告日对应最近一期的四个因子。
    - SUE: 从利润表"净利润"字段构造（本季 vs 过去 4 季）
    - ROE: 从 indicators "净资产收益率(%)"
    - NI_YoY: 从 indicators "净利润增长率(%)"
    - ACC: 净利润 - 经营性现金流量净额；除以平均总资产（这里近似用总资产快照）
    """
    # 三表关键字段
    profit = load_reports_field(reports, "净利润", "利润表").rename({"净利润": "net_profit"})
    cash = load_reports_field(reports, "经营活动产生的现金流量净额", "现金流量表").rename(
        {"经营活动产生的现金流量净额": "operating_cf"}
    )
    balance = load_reports_field(reports, "资产总计", "资产负债表").rename({"资产总计": "total_assets"})

    # 派生指标：ROE + 净利润同比
    # indicator 的 report_date 是 'YYYY-MM-DD' 格式，reports 的 report_date 是 'YYYYMMDD'
    # 统一到 'YYYYMMDD' 好 join
    ind = load_indicators_fields(indicators, ["净资产收益率(%)", "净利润增长率(%)"])
    ind = ind.with_columns(
        pl.col("report_date").str.replace_all("-", "").alias("report_date")
    )
    ind = ind.rename({"净资产收益率(%)": "roe", "净利润增长率(%)": "ni_yoy"})

    # 合并：以 profit 为主（有公告日），其它按 (vt_symbol, report_date) join
    base = profit.join(
        cash.select("vt_symbol", "report_date", "operating_cf"),
        on=["vt_symbol", "report_date"], how="left",
    ).join(
        balance.select("vt_symbol", "report_date", "total_assets"),
        on=["vt_symbol", "report_date"], how="left",
    ).join(
        ind, on=["vt_symbol", "report_date"], how="left",
    )

    # 转 date + 排序（同 vt_symbol 按 announce_date 升序）
    base = base.with_columns(
        parse_report_date("announce_date").alias("announce_date"),
        parse_report_date("report_date").alias("report_date_dt"),
    ).filter(pl.col("announce_date").is_not_null()).sort(["vt_symbol", "announce_date"])

    # SUE = (本季净利润 - 过去 4 季均值) / 过去 4 季 std
    base = base.with_columns(
        pl.col("net_profit").rolling_mean(4).over("vt_symbol").alias("np_mean_4"),
        pl.col("net_profit").rolling_std(4).over("vt_symbol").alias("np_std_4"),
    ).with_columns(
        pl.when(pl.col("np_std_4") > 0)
        .then((pl.col("net_profit") - pl.col("np_mean_4")) / pl.col("np_std_4"))
        .otherwise(None)
        .alias("sue")
    )

    # ACC = (净利润 - 经营性现金流) / 总资产
    base = base.with_columns(
        pl.when(pl.col("total_assets") > 0)
        .then((pl.col("net_profit") - pl.col("operating_cf")) / pl.col("total_assets"))
        .otherwise(None)
        .alias("acc")
    )

    return base.select("vt_symbol", "announce_date", "sue", "roe", "ni_yoy", "acc")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", default="data/silver/daily_bars_adjusted.parquet")
    parser.add_argument("--reports", default="data/akshare/financial_reports.parquet")
    parser.add_argument("--indicators", default="data/akshare/financial_indicators.parquet")
    parser.add_argument("--universe", default="data/universe/execution_universe.parquet")
    parser.add_argument("--industry-source", default="gics1", choices=["gics1", "sw1"])
    parser.add_argument("--label", default="gics1")
    parser.add_argument("--output-dir", default="outputs/factor_checks")
    args = parser.parse_args()

    print("加载 T6 财务数据 ...")
    reports = pl.read_parquet(args.reports)
    indicators = pl.read_parquet(args.indicators)
    print(f"reports: {reports.height} 行 / {reports.get_column('vt_symbol').unique().len()} 只")
    print(f"indicators: {indicators.height} 行 / {indicators.get_column('vt_symbol').unique().len()} 只")

    print("构造 PIT 基本面因子 ...")
    pit = build_pit_factor(reports, indicators)
    print(f"PIT 因子: {pit.height} 条记录, {pit.get_column('vt_symbol').unique().len()} 只")

    if args.industry_source == "sw1":
        members_path, prefix = Path("data/sector/sw1_members.parquet"), "SW1"
    else:
        members_path, prefix = Path("data/sector/sector_members.parquet"), "GICS1"

    print("加载行情 + 行业动量 ...")
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
        f"# 基本面因子检验：{args.label}",
        "",
        f"- 行业口径: {prefix}（相对强度基准）",
        f"- 样本区间: {trade_dates[0]} ~ {trade_dates[-1]}",
        f"- 因子定义:",
        f"  - `sue` = (本季净利润 - 过去 4 季均值) / 过去 4 季 std（预期正 IC，业绩超预期）",
        f"  - `roe` = akshare 净资产收益率(%)（预期正 IC，质量因子）",
        f"  - `ni_yoy` = akshare 净利润增长率(%)（预期正 IC，成长因子）",
        f"  - `acc` = (净利润 - 经营性现金流) / 总资产（预期负 IC，低应计 = 真实质量）",
        f"- PIT 对齐: 每个调仓日只用 announce_date < 调仓日的最新一期，asof join backward",
        f"- 前瞻收益 = 当期调仓日收盘到下期调仓日收盘（等比后复权），5 分层",
        f"- 局限: 行业成分为静态快照；不含交易成本；样本内统计；"
        f"indicators 表本身无 announce_date（此实现按 report_date join 三表拿公告日）",
        "",
    ]

    corr_snapshots: dict[str, pl.DataFrame] = {}

    for freq, rs_col, ppy in [
        ("monthly", "rs_60", 12),
        ("weekly", "rs_20", 52),
    ]:
        period_ends = build_period_ends(trade_dates, freq)

        # 构造截面：调仓日的因子（PIT） + 价量因子 + fwd_ret
        order = {d: i for i, d in enumerate(period_ends)}
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

        # PIT 对齐：每个调仓日 x 每只股票，取 announce_date < 调仓日的最新因子
        px_snap = px_snap.sort(["vt_symbol", "d"])
        pit_sorted = pit.sort(["vt_symbol", "announce_date"])
        combined = px_snap.join_asof(
            pit_sorted, left_on="d", right_on="announce_date", by="vt_symbol", strategy="backward",
        )

        pool = combined.filter(
            pl.col("fwd_ret").is_not_null()
            & pl.col("in_execution")
            & (pl.col("listed_bars") >= MIN_LISTED_BARS)
        )
        pool_size = pool.group_by("d").len().get_column("len").median() if pool.height else 0

        lines.append(f"## {freq} / all 池（{len(period_ends)} 期，池中位 {pool_size:.0f} 只）")
        lines.append("")
        lines.append("| 因子 | IC均值 | ICIR | t值 | IC>0 | Q1 | Q3 | Q5 | 顶底价差年化 | 价差t | 单调 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for factor in ["sue", "roe", "ni_yoy", "acc"]:
            r = evaluate_factor(pool, factor, ppy)
            q = r["q_means"]
            mono = {"up": "升", "down": "降", "no": "否"}[r["monotone"]]
            row = (
                f"| {factor} | {r['ic_mean']:+.3f} | {r['icir']:+.2f} | {r['t_stat']:+.2f} "
                f"| {r['pct_positive']:.0%} | {q[0] * 100:+.2f}% | {q[2] * 100:+.2f}% | {q[-1] * 100:+.2f}% "
                f"| {r['spread_ann'] * 100:+.1f}% | {r['spread_t']:+.2f} | {mono} |"
            )
            lines.append(row)
            print(row)
        lines.append("")
        corr_snapshots[f"{freq}/all"] = pool.select(
            "d", "sue", "roe", "ni_yoy", "acc", rs_col, "nh_252", "vol_ratio"
        )

    lines.append("## 与生产三因子的截面秩相关（均值）")
    lines.append("")
    lines.append("| 池 | 新因子 | rs | nh_252 | vol_ratio |")
    lines.append("|---|---|---|---|---|")
    for pool_key, pool in corr_snapshots.items():
        rs_col = "rs_20" if pool_key.startswith("weekly") else "rs_60"
        for new_f in ["sue", "roe", "ni_yoy", "acc"]:
            r_rs = cross_section_rank_corr(pool, new_f, rs_col)
            r_nh = cross_section_rank_corr(pool, new_f, "nh_252")
            r_vol = cross_section_rank_corr(pool, new_f, "vol_ratio")
            lines.append(f"| {pool_key} | {new_f} | {r_rs:+.2f} | {r_nh:+.2f} | {r_vol:+.2f} |")
    lines.append("")

    lines.append("## 闸门（handoff T2 + 011/013 教训）")
    lines.append("")
    lines.append("- 通过条件: |t| >= 2 且 与三因子截面相关 |ρ| < 0.5")
    lines.append("- **即使通过闸门，接入前必须做容量鲁棒性对照**（011 Amihud 教训）")
    lines.append("- 基本面因子理论上不与小盘规模挂钩，但仍要用 min_turnover 三档验证")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"fundamental_factors_{args.label}.md"
    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已写入 {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
