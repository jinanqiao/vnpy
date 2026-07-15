"""006 改造的测试：行业口径可配置（sw1）+ regime 市场状态开关模式。"""

from dataclasses import replace
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from vnpy.alpha.research.mainline.config import MainlineConfig
from vnpy.alpha.research.mainline.data_loader import load_industry_map
from vnpy.alpha.research.mainline.pipeline import run_mainline_signals
from vnpy.alpha.research.mainline.regime import market_momentum
from mainline_fixtures import (
    make_business_dates,
    make_execution_universe,
    make_industry_map,
    make_stock_bars,
    write_data_lake,
)

DATES = make_business_dates(date(2025, 1, 1), 60)

# 与 test_mainline_pipeline 相同的三行业结构：STRONG 最强、MID 次之、WEAK 下跌
INDUSTRIES = {
    "GICS1STRONG": [("S1.SZ", 0.015), ("S2.SZ", 0.014), ("S3.SZ", 0.013)],
    "GICS1MID": [("M1.SZ", 0.006), ("M2.SZ", 0.005), ("M3.SZ", 0.004)],
    "GICS1WEAK": [("W1.SZ", -0.005), ("W2.SZ", -0.005), ("W3.SZ", -0.005)],
}

SMALL_CONFIG = replace(
    MainlineConfig(),
    rebalance_freq="monthly",
    industry_mode="select",    # 008 后默认变为 none，这里钉住 006 时代的行为做对照
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


def build_lake(tmp_path: Path) -> MainlineConfig:
    """合成数据湖：GICS1 成分写 sector_members，同一批股票再写一份 SW1 成分（拆成 4 个行业）。"""
    all_bars = []
    members: dict[str, list[str]] = {}
    for industry, stocks in INDUSTRIES.items():
        members[industry] = [s for s, _ in stocks]
        for vt_symbol, daily_return in stocks:
            all_bars.append(make_stock_bars(vt_symbol, DATES, daily_return))
    bars = pl.concat(all_bars)

    sector_members = make_industry_map(members).rename({"industry": "sector"})
    symbols = [s for stocks in INDUSTRIES.values() for s, _ in stocks]
    universe = make_execution_universe(DATES, symbols)
    write_data_lake(tmp_path, bars, sector_members.select("sector", "vt_symbol"), universe, DATES)

    # SW1 口径：把 STRONG 行业拆成两个更细的行业，验证口径切换真的生效
    sw1 = pl.DataFrame(
        {
            "sector": ["SW1强一", "SW1强一", "SW1强二", "SW1中游", "SW1中游", "SW1中游",
                       "SW1弱势", "SW1弱势", "SW1弱势"],
            "vt_symbol": ["S1.SZ", "S2.SZ", "S3.SZ", "M1.SZ", "M2.SZ", "M3.SZ",
                          "W1.SZ", "W2.SZ", "W3.SZ"],
        }
    )
    sw1.write_parquet(tmp_path / "sector" / "sw1_members.parquet")

    return replace(SMALL_CONFIG, data_dir=str(tmp_path), output_dir=str(tmp_path / "outputs"))


def test_config_validation() -> None:
    with pytest.raises(ValueError, match="industry_source"):
        MainlineConfig(industry_source="citic")
    with pytest.raises(ValueError, match="industry_mode"):
        MainlineConfig(industry_mode="hybrid")
    with pytest.raises(ValueError, match="regime_top_n"):
        MainlineConfig(regime_top_n=0)


def test_industry_map_sw1_source(tmp_path: Path) -> None:
    """industry_source=sw1 时读申万成分表，行业名带 SW1 前缀。"""
    config = replace(build_lake(tmp_path), industry_source="sw1")
    industry_map = load_industry_map(config, [])

    industries = set(industry_map.get_column("industry").to_list())
    assert industries == {"SW1强一", "SW1强二", "SW1中游", "SW1弱势"}
    assert industry_map.height == 9


def test_market_momentum_equals_equal_weight_compound(tmp_path: Path) -> None:
    """全市场动量 = 全部股票等权日收益的复利，可手算对照。"""
    config = build_lake(tmp_path)
    bars = pl.read_parquet(Path(config.data_dir) / "normalized" / "daily_bars_all_a.parquet")
    rebalance_date = DATES[-1]

    momentum = market_momentum(bars, config, rebalance_date)

    # 手算：9 只股票日收益固定，等权平均后按 mom_window=8 复利
    daily_returns = [r for stocks in INDUSTRIES.values() for _, r in stocks]
    mean_daily = sum(daily_returns) / len(daily_returns)
    expected = (1 + mean_daily) ** config.mom_window - 1
    assert momentum == pytest.approx(expected, rel=1e-9)


def test_regime_mode_selects_across_market(tmp_path: Path) -> None:
    """regime 模式：有主线才建仓；候选池是全市场，不受主线行业限制。"""
    config = replace(build_lake(tmp_path), industry_mode="regime", regime_top_n=4)

    result = run_mainline_signals(config)
    stock_signals = result["stock_signals"]
    selection = result["selection"]

    assert not selection.is_empty()
    # 候选快照覆盖全市场 9 只股票（select 模式只会有主线行业的 6 只）
    for rebalance_date in stock_signals.get_column("rebalance_date").unique().to_list():
        period = stock_signals.filter(pl.col("rebalance_date") == rebalance_date)
        assert period.height == 9

    # 全池取前 regime_top_n
    per_period = selection.group_by("rebalance_date").len()
    assert per_period.get_column("len").max() <= 4

    # 入选者必须通过全部硬过滤
    selected_rows = stock_signals.filter(pl.col("selected"))
    for column in ["filter_history", "filter_tradable", "filter_ma_align", "filter_bias", "filter_nh"]:
        assert selected_rows.get_column(column).all()


def test_regime_mode_empty_when_no_mainline(tmp_path: Path) -> None:
    """全市场下跌时行业层三关全灭 → regime 模式同样空清单（状态开关生效）。"""
    all_bars = []
    members: dict[str, list[str]] = {}
    for industry, stocks in INDUSTRIES.items():
        members[industry] = [s for s, _ in stocks]
        for vt_symbol, _ in stocks:
            all_bars.append(make_stock_bars(vt_symbol, DATES, -0.004))  # 全部下跌
    bars = pl.concat(all_bars)
    sector_members = make_industry_map(members).rename({"industry": "sector"})
    symbols = [s for stocks in INDUSTRIES.values() for s, _ in stocks]
    universe = make_execution_universe(DATES, symbols)
    write_data_lake(tmp_path, bars, sector_members.select("sector", "vt_symbol"), universe, DATES)

    config = replace(
        SMALL_CONFIG,
        data_dir=str(tmp_path),
        output_dir=str(tmp_path / "outputs"),
        industry_mode="regime",
    )
    result = run_mainline_signals(config)

    assert result["selection"].is_empty()


def test_regime_mode_reproducible(tmp_path: Path) -> None:
    """regime 模式同输入两次运行产物逐字节一致。"""
    config = replace(build_lake(tmp_path), industry_mode="regime")
    first = run_mainline_signals(replace(config, name="regime_a"))
    second = run_mainline_signals(replace(config, name="regime_b"))

    for filename in ["industry_signals.parquet", "stock_signals.parquet", "selection.parquet"]:
        bytes_a = (first["output_dir"] / filename).read_bytes()
        bytes_b = (second["output_dir"] / filename).read_bytes()
        assert bytes_a == bytes_b, f"{filename} 两次运行结果不一致"
