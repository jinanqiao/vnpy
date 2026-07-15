"""数据加载 + 流水线端到端测试：合成数据湖上验证完整跑通与产物契约。"""

from dataclasses import replace
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from vnpy.alpha.research.mainline.config import MainlineConfig, load_config_from_json
from vnpy.alpha.research.mainline.data_loader import (
    build_rebalance_dates,
    load_daily_bars,
    load_industry_map,
)
from vnpy.alpha.research.mainline.pipeline import run_mainline_signals
from vnpy.alpha.research.mainline.stocks import REJECT_REASONS
from mainline_fixtures import (
    make_business_dates,
    make_execution_universe,
    make_industry_map,
    make_stock_bars,
    write_data_lake,
)

DATES = make_business_dates(date(2025, 1, 1), 60)

# 三个行业走势可控：STRONG 最强、MID 次之、WEAK 下跌
INDUSTRIES = {
    "GICS1STRONG": [("S1.SZ", 0.015), ("S2.SZ", 0.014), ("S3.SZ", 0.013)],
    "GICS1MID": [("M1.SZ", 0.006), ("M2.SZ", 0.005), ("M3.SZ", 0.004)],
    "GICS1WEAK": [("W1.SZ", -0.005), ("W2.SZ", -0.005), ("W3.SZ", -0.005)],
}

SMALL_CONFIG = replace(
    MainlineConfig(),
    rebalance_freq="monthly",  # 本套测试的手算对照按月频构造
    industry_mode="select",    # 008 后默认变为 none，这里钉住旧的行业层行为
    trend_filters_enabled=True,
    score_weights=(0.40, 0.35, 0.25),
    mom_window=8,
    confirm_window=4,
    breadth_window=8,
    min_listed_bars=20,
    nh_window=20,
    ma_windows=(3, 5, 8),
    ma_slope_lag=2,
    vol_short=3,
    vol_long=6,
    min_industry_members=2,
    industry_top_n=2,
    industry_rank_gate=2,
    stocks_per_industry_min=2,
    stocks_per_industry_max=3,
)


def month_end_dates(dates: list[date]) -> list[date]:
    """合成日历里每个自然月的最后一个交易日（测试自己算一遍作对照）。

    日历的最后一个月视为"尚未收官"，不产生调仓日（与 data_loader 的约定一致）。
    """
    by_month: dict[str, date] = {}
    for d in dates:
        key = d.strftime("%Y-%m")
        by_month[key] = max(by_month.get(key, d), d)
    last_month = max(by_month.keys())
    return sorted(v for k, v in by_month.items() if k != last_month)


def build_lake(tmp_path: Path, blocked: set[tuple[date, str]] | None = None) -> MainlineConfig:
    """在临时目录搭一个最小可用的数据湖，返回指向它的配置。"""
    all_bars = []
    members: dict[str, list[str]] = {}
    for industry, stocks in INDUSTRIES.items():
        members[industry] = [s for s, _ in stocks]
        for vt_symbol, daily_return in stocks:
            all_bars.append(make_stock_bars(vt_symbol, DATES, daily_return))
    bars = pl.concat(all_bars)

    sector_members = make_industry_map(members).rename({"industry": "sector"})
    # 混入一个非 GICS1 的宽基板块，验证会被过滤掉
    sector_members = pl.concat(
        [sector_members, pl.DataFrame({"vt_symbol": ["S1.SZ"], "sector": ["上证A股"]})]
    )

    symbols = [s for stocks in INDUSTRIES.values() for s, _ in stocks]
    universe = make_execution_universe(DATES, symbols, blocked=blocked)
    write_data_lake(tmp_path, bars, sector_members.select("sector", "vt_symbol"), universe, DATES)

    return replace(
        SMALL_CONFIG,
        data_dir=str(tmp_path),
        output_dir=str(tmp_path / "outputs"),
    )


def test_industry_map_keeps_only_gics1(tmp_path: Path) -> None:
    config = build_lake(tmp_path)
    logs: list[dict] = []

    industry_map = load_industry_map(config, logs)

    assert set(industry_map.get_column("industry").to_list()) == set(INDUSTRIES.keys())
    assert industry_map.height == 9  # 宽基板块那行被过滤，每只股票只有一行


def test_missing_required_column_raises(tmp_path: Path) -> None:
    config = build_lake(tmp_path)
    bars_path = tmp_path / "normalized" / "daily_bars_all_a.parquet"
    pl.read_parquet(bars_path).drop("close").write_parquet(bars_path)

    with pytest.raises(ValueError, match="close"):
        load_daily_bars(config, [])


def test_rebalance_dates_are_month_end_trading_days(tmp_path: Path) -> None:
    config = build_lake(tmp_path)
    bars = load_daily_bars(config, [])

    assert build_rebalance_dates(config, bars) == month_end_dates(DATES)


def week_end_dates(dates: list[date]) -> list[date]:
    """合成日历里每个 ISO 周的最后一个交易日（测试自己算一遍作对照）。

    日历的最后一周视为"尚未收官"，不产生调仓日（与 data_loader 的约定一致）。
    """
    by_week: dict[tuple, date] = {}
    for d in dates:
        key = d.isocalendar()[:2]
        by_week[key] = max(by_week.get(key, d), d)
    last_week = max(by_week.keys())
    return sorted(v for k, v in by_week.items() if k != last_week)


def test_rebalance_dates_weekly(tmp_path: Path) -> None:
    """周频调仓：每个 ISO 周的最后一个交易日，末尾不完整周被丢掉。"""
    config = replace(build_lake(tmp_path), rebalance_freq="weekly")
    bars = load_daily_bars(config, [])

    weekly = build_rebalance_dates(config, bars)
    assert weekly == week_end_dates(DATES)
    assert all(d.weekday() == 4 for d in weekly)  # 合成日历无节假日，周末调仓日都是周五


def test_suspect_ex_dividend_drop_logged(tmp_path: Path) -> None:
    """人为注入一根 -20% 的大阴线，应记进数据质量日志（R3）。"""
    config = build_lake(tmp_path)
    bars_path = tmp_path / "normalized" / "daily_bars_all_a.parquet"
    bars = pl.read_parquet(bars_path)
    crash_day = DATES[30]
    bars = bars.with_columns(
        pl.when((pl.col("vt_symbol") == "M1.SZ") & (pl.col("datetime") == crash_day))
        .then(pl.col("close") * 0.8)
        .otherwise(pl.col("close"))
        .alias("close")
    )
    bars.write_parquet(bars_path)

    logs: list[dict] = []
    load_daily_bars(config, logs)

    suspects = [log for log in logs if log["type"] == "suspect_ex_dividend_drop"]
    assert len(suspects) == 1
    assert suspects[0]["vt_symbol"] == "M1.SZ"
    assert suspects[0]["datetime"] == str(crash_day)


def test_config_json_rejects_unknown_field(tmp_path: Path) -> None:
    config_path = tmp_path / "override.json"
    config_path.write_text('{"industry_topn": 5}', encoding="utf-8")

    with pytest.raises(ValueError, match="industry_topn"):
        load_config_from_json(config_path)


def test_end_to_end_artifacts_and_invariants(tmp_path: Path) -> None:
    """完整跑一遍：六件套齐全，行业/个股/清单满足所有关键不变量（SC-1 ~ SC-6）。"""
    second_rebalance = month_end_dates(DATES)[1]
    config = build_lake(tmp_path, blocked={(second_rebalance, "S1.SZ")})

    result = run_mainline_signals(config)
    output_dir = result["output_dir"]

    for filename in [
        "config.json", "industry_signals.parquet", "stock_signals.parquet",
        "selection.parquet", "data_quality.json", "report.md",
    ]:
        assert (output_dir / filename).exists(), f"缺少产物 {filename}"

    industry_signals = result["industry_signals"]
    stock_signals = result["stock_signals"]
    selection = result["selection"]

    # 行业层：每期入选的只有 STRONG 和 MID（WEAK 排名第 3 过不了 gate=2）
    for rebalance_date in industry_signals.get_column("rebalance_date").unique().to_list():
        picked = (
            industry_signals.filter((pl.col("rebalance_date") == rebalance_date) & pl.col("selected"))
            .get_column("industry").sort().to_list()
        )
        assert picked == ["GICS1MID", "GICS1STRONG"]

    # 个股层：入选清单与快照一致；入选者五项过滤全通过、无剔除原因
    selected_rows = stock_signals.filter(pl.col("selected"))
    assert selection.height == selected_rows.height
    filter_columns = ["filter_history", "filter_tradable", "filter_ma_align", "filter_bias", "filter_nh"]
    for column in filter_columns:
        assert selected_rows.get_column(column).all()
    assert selected_rows.get_column("reject_reason").null_count() == selected_rows.height

    # 剔除原因只能取契约里的固定枚举
    reasons = set(stock_signals.get_column("reject_reason").drop_nulls().to_list())
    assert reasons <= set(REJECT_REASONS)

    # 当日停牌的 S1.SZ 在第二个调仓日被记为 not_tradable 且未入选
    blocked_row = stock_signals.filter(
        (pl.col("rebalance_date") == second_rebalance) & (pl.col("vt_symbol") == "S1.SZ")
    ).row(0, named=True)
    assert blocked_row["reject_reason"] == "not_tradable"
    assert blocked_row["selected"] is False

    # 总分可以由三个排名和权重复算出来（SC-4 可追溯性）
    w_rs, w_nh, w_vol = config.score_weights
    recomputed = selected_rows.with_columns(
        (w_rs * pl.col("rank_rs") + w_nh * pl.col("rank_nh") + w_vol * pl.col("rank_vol")).alias("expected")
    )
    assert (recomputed.get_column("score") - recomputed.get_column("expected")).abs().max() < 1e-12

    # 每行业入选数不超过上限
    per_industry = selection.group_by(["rebalance_date", "industry"]).len()
    assert per_industry.get_column("len").max() <= config.stocks_per_industry_max


def test_live_data_gate_blocks_signal_generation(tmp_path: Path) -> None:
    config = replace(build_lake(tmp_path), data_gate_mode="live", end="2025-03-31")

    with pytest.raises(RuntimeError, match="数据门禁失败"):
        run_mainline_signals(config)


def test_rerun_is_reproducible(tmp_path: Path) -> None:
    """同样的数据和参数跑两次，三个 parquet 产物逐字节一致（SC-6）。"""
    config = build_lake(tmp_path)
    first = run_mainline_signals(replace(config, name="run_a"))
    second = run_mainline_signals(replace(config, name="run_b"))

    for filename in ["industry_signals.parquet", "stock_signals.parquet", "selection.parquet"]:
        bytes_a = (first["output_dir"] / filename).read_bytes()
        bytes_b = (second["output_dir"] / filename).read_bytes()
        assert bytes_a == bytes_b, f"{filename} 两次运行结果不一致"
