"""008 改造的测试：去掉行业层（industry_mode=none）+ rs/vol 反向权重 + 简化硬过滤。"""

from dataclasses import replace
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from vnpy.alpha.research.mainline.config import MainlineConfig
from vnpy.alpha.research.mainline.pipeline import run_mainline_signals
from vnpy.alpha.research.mainline.stocks import apply_stock_filters, rank_and_score
from mainline_fixtures import (
    make_business_dates,
    make_execution_universe,
    make_industry_map,
    make_stock_bars,
    write_data_lake,
)

TODAY = date(2025, 6, 30)
DATES = make_business_dates(date(2025, 1, 1), 60)

# 三组走势：STRONG 强、MID 中、WEAK 跌——none 模式下不分行业，全池一起排
INDUSTRIES = {
    "GICS1STRONG": [("S1.SZ", 0.015), ("S2.SZ", 0.014), ("S3.SZ", 0.013)],
    "GICS1MID": [("M1.SZ", 0.006), ("M2.SZ", 0.005), ("M3.SZ", 0.004)],
    "GICS1WEAK": [("W1.SZ", -0.005), ("W2.SZ", -0.005), ("W3.SZ", -0.005)],
}

SMALL_CONFIG = replace(
    MainlineConfig(),
    rebalance_freq="monthly",
    mom_window=8,
    confirm_window=4,
    breadth_window=8,
    min_listed_bars=20,
    nh_window=20,
    ma_windows=(3, 5, 8),
    ma_slope_lag=2,
    vol_short=3,
    vol_long=6,
    regime_top_n=4,
)


def build_lake(tmp_path: Path) -> MainlineConfig:
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
    return replace(SMALL_CONFIG, data_dir=str(tmp_path), output_dir=str(tmp_path / "outputs"))


def test_new_defaults() -> None:
    """008 后的出厂默认：无行业层、简化过滤、rs/vol 反向权重。"""
    config = MainlineConfig()
    assert config.industry_mode == "none"
    assert config.trend_filters_enabled is False
    assert config.score_weights == (-0.40, 0.35, -0.25)


def make_feature_row(vt_symbol: str, **overrides) -> dict:
    row = {
        "vt_symbol": vt_symbol,
        "industry": "GICS1A",
        "listed_bars": 300,
        "close": 11.0,
        "ma20": 10.5,
        "ma60": 10.0,
        "ma120": 9.5,
        "ma20_lagged": 10.2,
        "high_max": 12.0,
        "turnover_short": 1.5e6,
        "turnover_long": 1.0e6,
    }
    row.update(overrides)
    return row


def test_simplified_filters_keep_only_history_and_tradable() -> None:
    """trend_filters_enabled=False：趋势三关全部放行，必要两项照常拦截。"""
    config = replace(MainlineConfig(), trend_filters_enabled=False)
    features = pl.DataFrame(
        [
            make_feature_row("MAOK.SZ", ma20=9.0),          # 均线空头，旧规则会拦
            make_feature_row("BIAS.SZ", close=15.0),        # 乖离 43%，旧规则会拦
            make_feature_row("FAR.SZ", close=8.0),          # 距新高 67%，旧规则会拦
            make_feature_row("NEW.SZ", listed_bars=100),    # 上市不满一年，仍要拦
            make_feature_row("HALT.SZ"),                    # 当日不可交易，仍要拦
        ]
    )
    universe = make_execution_universe(
        [TODAY], features.get_column("vt_symbol").to_list(), blocked={(TODAY, "HALT.SZ")}
    )
    result = apply_stock_filters(features, universe, config, TODAY)

    reasons = dict(result.select("vt_symbol", "reject_reason").rows())
    assert reasons["MAOK.SZ"] is None
    assert reasons["BIAS.SZ"] is None
    assert reasons["FAR.SZ"] is None
    assert reasons["NEW.SZ"] == "insufficient_history"
    assert reasons["HALT.SZ"] == "not_tradable"


def test_negative_weights_reverse_factor_direction() -> None:
    """负权重 = 因子反向：rs 最弱的股票应拿到最高的 rs 贡献。"""
    config = replace(MainlineConfig(), score_weights=(-1.0, 0.0, 0.0))
    factors = pl.DataFrame(
        {
            "vt_symbol": ["WEAKRS.SZ", "MIDRS.SZ", "STRONGRS.SZ"],
            "reject_reason": [None, None, None],
            "rs_60": [-0.10, 0.00, 0.10],
            "nh_252": [0.9, 0.9, 0.9],
            "vol_ratio": [1.0, 1.0, 1.0],
            "ret_20": [0.01, 0.01, 0.01],
        }
    )
    scored = rank_and_score(factors, config).sort("score", descending=True)
    assert scored.get_column("vt_symbol").to_list() == ["WEAKRS.SZ", "MIDRS.SZ", "STRONGRS.SZ"]


def test_none_mode_selects_every_period_across_market(tmp_path: Path) -> None:
    """none 模式：没有行业闸门，每个调仓期都全市场选股，行业信号表为空。"""
    config = build_lake(tmp_path)
    result = run_mainline_signals(config)

    assert result["industry_signals"].is_empty()
    selection = result["selection"]
    stock_signals = result["stock_signals"]
    assert not selection.is_empty()

    # 候选快照覆盖全市场 9 只；每期入选数 = regime_top_n
    for rebalance_date in stock_signals.get_column("rebalance_date").unique().to_list():
        period = stock_signals.filter(pl.col("rebalance_date") == rebalance_date)
        assert period.height == 9
        assert period.filter(pl.col("selected")).height == config.regime_top_n

    # 行业层关掉后 selection 里的主线月龄应全部为空
    assert selection.get_column("industry_streak").is_null().all()

    # rs 反向的默认权重下，动量最强的 STRONG 组应该一只都选不进
    # （旧的正向权重下 STRONG 组是必选项；手算顺位：M3 > M2 > W 组 > M1 > S 组）
    first_period = selection.filter(
        pl.col("rebalance_date") == selection.get_column("rebalance_date").min()
    )
    selected_symbols = set(first_period.get_column("vt_symbol").to_list())
    assert selected_symbols.isdisjoint({"S1.SZ", "S2.SZ", "S3.SZ"})
    assert {"M3.SZ", "M2.SZ", "W1.SZ", "W2.SZ"} == selected_symbols

    report = (result["output_dir"] / "report.md").read_text(encoding="utf-8")
    assert "行业层已关闭" in report


def test_none_mode_even_when_market_falls(tmp_path: Path) -> None:
    """none 模式没有"无主线空仓"状态：全市场下跌时依然出满额清单（择时交给回测层）。"""
    all_bars = []
    members: dict[str, list[str]] = {}
    for industry, stocks in INDUSTRIES.items():
        members[industry] = [s for s, _ in stocks]
        for vt_symbol, _ in stocks:
            all_bars.append(make_stock_bars(vt_symbol, DATES, -0.004))
    bars = pl.concat(all_bars)
    sector_members = make_industry_map(members).rename({"industry": "sector"})
    symbols = [s for stocks in INDUSTRIES.values() for s, _ in stocks]
    universe = make_execution_universe(DATES, symbols)
    write_data_lake(tmp_path, bars, sector_members.select("sector", "vt_symbol"), universe, DATES)

    config = replace(SMALL_CONFIG, data_dir=str(tmp_path), output_dir=str(tmp_path / "outputs"))
    result = run_mainline_signals(config)

    per_period = result["selection"].group_by("rebalance_date").len()
    assert not per_period.is_empty()
    assert per_period.get_column("len").min() == config.regime_top_n


def test_none_mode_reproducible(tmp_path: Path) -> None:
    """none 模式同输入两次运行产物逐字节一致。"""
    config = build_lake(tmp_path)
    first = run_mainline_signals(replace(config, name="none_a"))
    second = run_mainline_signals(replace(config, name="none_b"))

    for filename in ["industry_signals.parquet", "stock_signals.parquet", "selection.parquet"]:
        bytes_a = (first["output_dir"] / filename).read_bytes()
        bytes_b = (second["output_dir"] / filename).read_bytes()
        assert bytes_a == bytes_b, f"{filename} 两次运行结果不一致"
