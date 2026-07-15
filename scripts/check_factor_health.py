"""生产三因子健康度季检（T3）：滚动 12 期 Rank IC vs 生产权重方向。

对生产打分层用的三个因子 rs_{mom_window} / nh_252 / vol_ratio 跑滚动
Rank IC（默认 12 期），把每个因子的近期 IC 方向与"权重期望的方向"对齐检查——
权重为负 = 期望负 IC（反向使用），权重为正 = 期望正 IC。任一因子不一致时
脚本返回 exit code 1 并醒目告警，提醒用户重新审视权重是否还成立。

用户会定期（如每季度）手动跑一次；配合 outputs/factor_checks/ 的历史检验，
监控 2026 年出现的"因子方向翻正"信号是否持续。

用法:
    python3 scripts/check_factor_health.py                          # 月频，默认生产权重
    python3 scripts/check_factor_health.py --freq weekly            # 周频
    python3 scripts/check_factor_health.py --recent 12 --trend 4    # 自定义窗口
    python3 scripts/check_factor_health.py --score-weights='-0.40,0.35,-0.25'

退出码:
    0 = 三个因子当前 IC 方向都与权重期望一致
    1 = 任一因子方向翻转（醒目告警 + 打印分歧的因子）
    2 = 数据异常（样本不足 / 未找到数据文件）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import polars as pl
from scipy import stats

MIN_LISTED_BARS = 252
VOL_CAP = 3.0


def load_stock_features(bars_path: Path, mom_window: int, confirm_window: int) -> pl.DataFrame:
    """逐日逐股滚动特征：rs/nh/vol 三因子 + 上市天数。窗口参数与生产 config 对齐。"""
    bars = pl.read_parquet(
        bars_path, columns=["datetime", "vt_symbol", "close", "high", "turnover"]
    ).with_columns(pl.col("datetime").cast(pl.Date).alias("d")).sort(["vt_symbol", "d"])

    over = {"partition_by": "vt_symbol"}
    return bars.with_columns(
        (pl.col("close") / pl.col("close").shift(mom_window).over(**over) - 1).alias("ret_main"),
        (pl.col("close") / pl.col("close").shift(confirm_window).over(**over) - 1).alias("ret_confirm"),
        (pl.col("close") / pl.col("high").rolling_max(252).over(**over)).alias("nh_252"),
        (
            pl.col("turnover").rolling_mean(20).over(**over)
            / pl.col("turnover").rolling_mean(60).over(**over)
        ).clip(upper_bound=VOL_CAP).alias("vol_ratio"),
        pl.int_range(pl.len()).over("vt_symbol").alias("listed_bars"),
    )


def load_industry_momentum(bars: pl.DataFrame, members_path: Path, prefix: str, mom_window: int):
    """行业等权动量（主窗口），逐日。"""
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
        (pl.col("idx") / pl.col("idx").shift(mom_window).over("industry") - 1).alias("ind_mom"),
    )
    return momentum.select("industry", "d", "ind_mom"), members


def build_period_ends(trade_dates: list, freq: str) -> list:
    """把日历切成周末/月末的调仓日序列，剔除最后一期（无 fwd_ret）。"""
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
    """调仓日截面：三因子 + 下一持有期收益（fwd_ret）。"""
    order = {d: i for i, d in enumerate(period_ends)}
    snap = (
        features.filter(pl.col("d").is_in(period_ends))
        .join(members, on="vt_symbol", how="inner")
        .join(industry_momentum, on=["industry", "d"], how="left")
        .join(universe, left_on=["d", "vt_symbol"], right_on=["datetime", "vt_symbol"], how="left")
        .with_columns(
            pl.col("in_execution").fill_null(False),
            (pl.col("ret_main") - pl.col("ind_mom")).alias("rs"),
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


def rank_ic_per_period(pool: pl.DataFrame, factor: str) -> list[tuple]:
    """每个调仓日截面的 Rank IC（Spearman），返回 [(date, ic), ...]。"""
    data = pool.select("d", factor, "fwd_ret").drop_nulls()
    out = []
    for (dates_tuple, group) in data.group_by("d"):
        if len(group) >= 30:
            ic, _ = stats.spearmanr(group[factor].to_list(), group["fwd_ret"].to_list())
            out.append((dates_tuple[0], ic))
    out.sort(key=lambda x: x[0])
    return out


def summarize_recent(ic_series: list[tuple], recent_n: int) -> dict:
    """最近 recent_n 期的 IC 均值 / t 值 / 正比例。"""
    tail = ic_series[-recent_n:]
    if not tail:
        return {"n": 0, "mean": float("nan"), "t": float("nan"), "pct_positive": float("nan")}
    values = pl.Series([ic for _, ic in tail])
    n = values.len()
    mean, std = values.mean(), values.std()
    return {
        "n": n,
        "mean": mean,
        "t": mean / std * (n ** 0.5) if std else float("nan"),
        "pct_positive": (values > 0).mean(),
        "start": tail[0][0],
        "end": tail[-1][0],
    }


def parse_score_weights(text: str) -> tuple[float, float, float]:
    parts = [float(x) for x in text.split(",")]
    if len(parts) != 3:
        raise ValueError(f'--score-weights 需要 3 个数，如 "-0.40,0.35,-0.25"，当前: {text}')
    return (parts[0], parts[1], parts[2])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", default="data/silver/daily_bars_adjusted.parquet")
    parser.add_argument("--universe", default="data/universe/execution_universe.parquet")
    parser.add_argument("--industry-source", default="gics1", choices=["gics1", "sw1"])
    parser.add_argument("--freq", default="monthly", choices=["weekly", "monthly"])
    parser.add_argument(
        "--score-weights", default="-0.40,0.35,-0.25",
        help='生产权重 "rs,nh,vol"（默认 -0.40,0.35,-0.25，与 CLAUDE.md 一致）',
    )
    parser.add_argument("--recent", type=int, default=12, help="最近 N 期 IC 均值判断方向（默认 12）")
    parser.add_argument("--trend", type=int, default=4, help="额外打印最近 N 期逐期 IC（默认 4）")
    args = parser.parse_args()

    w_rs, w_nh, w_vol = parse_score_weights(args.score_weights)
    mom_window, confirm_window = (60, 20) if args.freq == "monthly" else (20, 5)
    ppy = 12 if args.freq == "monthly" else 52
    label_rs = f"rs_{mom_window}"

    members_path, prefix = (
        (Path("data/sector/sw1_members.parquet"), "SW1")
        if args.industry_source == "sw1"
        else (Path("data/sector/sector_members.parquet"), "GICS1")
    )

    bars_path = Path(args.bars)
    if not bars_path.exists():
        print(f"[FATAL] 行情数据不存在: {bars_path}", file=sys.stderr)
        return 2

    print(f"[INFO] 频率={args.freq} 行业={prefix} 窗口=mom{mom_window}/conf{confirm_window}")
    print(f"[INFO] 生产权重: rs={w_rs:+.2f} nh={w_nh:+.2f} vol={w_vol:+.2f}")

    features = load_stock_features(bars_path, mom_window, confirm_window)
    industry_momentum, members = load_industry_momentum(features, members_path, prefix, mom_window)
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
    period_ends = build_period_ends(trade_dates, args.freq)
    snap = build_snapshot(features, industry_momentum, members, universe, period_ends)

    factors = [
        (label_rs, "rs", w_rs),
        ("nh_252", "nh_252", w_nh),
        ("vol_ratio", "vol_ratio", w_vol),
    ]

    print(f"\n[INFO] 样本区间: {period_ends[0]} ~ {period_ends[-1]}（共 {len(period_ends)} 期）")
    print(f"[INFO] 判断口径: 最近 {args.recent} 期 IC 均值的符号 vs 权重期望符号（负权重 = 期望负 IC）\n")

    print(f"| 因子 | 权重 | 期望方向 | 最近{args.recent}期IC均值 | t值 | IC>0比例 | 实际方向 | 状态 |")
    print("|---|---|---|---|---|---|---|---|")

    mismatched = []
    trend_rows = []
    for label, factor_col, weight in factors:
        ic_series = rank_ic_per_period(snap, factor_col)
        recent = summarize_recent(ic_series, args.recent)
        expected_sign = "负" if weight < 0 else ("正" if weight > 0 else "中性")
        actual_sign = "负" if recent["mean"] < 0 else ("正" if recent["mean"] > 0 else "零")
        expected_sign_char = "-" if weight < 0 else ("+" if weight > 0 else "0")
        actual_sign_char = "-" if recent["mean"] < 0 else ("+" if recent["mean"] > 0 else "0")
        ok = (expected_sign_char == actual_sign_char) or weight == 0
        status = "✓" if ok else "⚠ 翻转"
        if not ok:
            mismatched.append((label, weight, recent["mean"]))
        print(
            f"| {label} | {weight:+.2f} | {expected_sign} | {recent['mean']:+.4f} "
            f"| {recent['t']:+.2f} | {recent['pct_positive']:.0%} | {actual_sign} | {status} |"
        )
        # 最近 trend 期逐期
        trend_rows.append((label, ic_series[-args.trend:]))

    print(f"\n== 最近 {args.trend} 期逐期 Rank IC ==")
    print("| 因子 | " + " | ".join(str(d) for d, _ in trend_rows[0][1]) + " |")
    print("|---|" + "---|" * len(trend_rows[0][1]))
    for label, tail in trend_rows:
        print(f"| {label} | " + " | ".join(f"{ic:+.3f}" for _, ic in tail) + " |")

    if mismatched:
        print(f"\n{'!' * 60}")
        print(f"⚠ 警告：{len(mismatched)} 个因子的当前 IC 方向与生产权重期望方向不一致：")
        for label, weight, mean_ic in mismatched:
            print(f"  - {label}: 权重={weight:+.2f}（期望 {'负' if weight<0 else '正'} IC），"
                  f"实际最近 {args.recent} 期均值 = {mean_ic:+.4f}")
        print("请审视权重是否还成立；参考 outputs/factor_checks/ 里的历史检验。")
        print("!" * 60)
        return 1

    print("\n[OK] 三因子方向与生产权重期望一致，未发现方向翻转。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
