"""010 改造的测试：max_per_industry 行业上限约束（none/regime 模式共用）。

这个文件钉住的 spec：
    - max_per_industry=0（默认）时行为与 008 完全一致，逐字节回归；
    - >0 时超出上限的个股不入选，名额顺延给全局排名的下一位（其他行业）。
"""

from dataclasses import replace
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from vnpy.alpha.research.mainline.config import MainlineConfig
from vnpy.alpha.research.mainline.pipeline import run_mainline_signals
from vnpy.alpha.research.mainline.regime import select_stocks_global
from mainline_fixtures import (
    make_business_dates,
    make_execution_universe,
    make_industry_map,
    make_stock_bars,
    write_data_lake,
)

DATES = make_business_dates(date(2025, 1, 1), 60)

# 合成"行业霸榜"数据：行业 A 有 8 只强势股（会在无约束时占满前排），
# 行业 B/C 各 4 只依次减弱，共 16 只。日收益全部不同，避免同分歧义。
INDUSTRIES: dict[str, list[tuple[str, float]]] = {
    "GICS1A": [
        ("A1.SZ", 0.020),
        ("A2.SZ", 0.019),
        ("A3.SZ", 0.018),
        ("A4.SZ", 0.017),
        ("A5.SZ", 0.016),
        ("A6.SZ", 0.015),
        ("A7.SZ", 0.014),
        ("A8.SZ", 0.013),
    ],
    "GICS1B": [
        ("B1.SZ", 0.012),
        ("B2.SZ", 0.011),
        ("B3.SZ", 0.010),
        ("B4.SZ", 0.009),
    ],
    "GICS1C": [
        ("C1.SZ", 0.008),
        ("C2.SZ", 0.007),
        ("C3.SZ", 0.006),
        ("C4.SZ", 0.005),
    ],
}

# 用"正向权重 + rs 单因子"简化打分：score = rank_rs，
# 打分完全由 rs_60 决定，rs_60 单调对应日收益，容易手算对照。
SMALL_CONFIG = replace(
    MainlineConfig(),
    rebalance_freq="monthly",
    industry_mode="none",
    score_weights=(1.0, 0.0, 0.0),
    mom_window=8,
    confirm_window=4,
    breadth_window=8,
    min_listed_bars=20,
    nh_window=20,
    ma_windows=(3, 5, 8),
    ma_slope_lag=2,
    vol_short=3,
    vol_long=6,
    regime_top_n=6,
)


def build_lake(tmp_path: Path) -> MainlineConfig:
    all_bars: list[pl.DataFrame] = []
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


def test_default_max_per_industry_is_zero() -> None:
    """出厂默认 max_per_industry=0，确保默认行为不变。"""
    assert MainlineConfig().max_per_industry == 0


def test_config_validation_rejects_negative() -> None:
    with pytest.raises(ValueError, match="max_per_industry"):
        MainlineConfig(max_per_industry=-1)


def test_cap_zero_matches_no_cap_selection(tmp_path: Path) -> None:
    """cap=0 与 cap=999（大到从不触发）在这份合成数据上应给出完全一致的入选清单。

    差异只可能出现在超出上限的名额顺延逻辑上；行业上限不触发时两条代码路径
    应该给出同样的入选结果（各列均一致）。
    """
    config = build_lake(tmp_path)
    result_zero = run_mainline_signals(replace(config, name="cap_zero", max_per_industry=0))
    result_large = run_mainline_signals(replace(config, name="cap_large", max_per_industry=999))

    a = result_zero["selection"].select(["rebalance_date", "vt_symbol", "industry", "industry_rank"])
    b = result_large["selection"].select(["rebalance_date", "vt_symbol", "industry", "industry_rank"])
    assert a.equals(b)


def test_industry_cap_promotes_slots_to_next_industry(tmp_path: Path) -> None:
    """cap=3, regime_top_n=6: 手算期望 = A1..A3 + B1..B3（A4 及之后被上限压回，让位给 B）。

    无约束时前 6 名会是 A1..A6（行业 A 独占）；加行业上限后 A 只能贡献 3 只，
    剩下 3 个名额顺延给全局排名的下一位——B 组前 3 只。
    """
    config = replace(build_lake(tmp_path), max_per_industry=3, regime_top_n=6)
    result = run_mainline_signals(config)
    selection = result["selection"]

    assert not selection.is_empty()
    first_period = selection.filter(
        pl.col("rebalance_date") == selection.get_column("rebalance_date").min()
    )
    selected = set(first_period.get_column("vt_symbol").to_list())
    assert selected == {"A1.SZ", "A2.SZ", "A3.SZ", "B1.SZ", "B2.SZ", "B3.SZ"}

    per_industry = first_period.group_by("industry").len()
    counts = dict(per_industry.rows())
    assert counts["GICS1A"] == 3
    assert counts["GICS1B"] == 3


def test_industry_cap_when_all_industries_below_limit(tmp_path: Path) -> None:
    """上限 >= 每个行业的存活股数时，入选与不限制情况完全一致。"""
    config = replace(build_lake(tmp_path), max_per_industry=8, regime_top_n=6)
    baseline_config = replace(config, name="no_cap", max_per_industry=0)
    result = run_mainline_signals(replace(config, name="with_cap"))
    baseline = run_mainline_signals(baseline_config)

    a = result["selection"].select(["rebalance_date", "vt_symbol", "industry_rank"])
    b = baseline["selection"].select(["rebalance_date", "vt_symbol", "industry_rank"])
    assert a.equals(b)


def test_industry_cap_reproducible(tmp_path: Path) -> None:
    """开行业上限后两次运行仍逐字节一致（复现性硬约束）。"""
    config = replace(build_lake(tmp_path), max_per_industry=3, regime_top_n=6)
    first = run_mainline_signals(replace(config, name="cap_a"))
    second = run_mainline_signals(replace(config, name="cap_b"))
    for filename in ["stock_signals.parquet", "selection.parquet"]:
        bytes_a = (first["output_dir"] / filename).read_bytes()
        bytes_b = (second["output_dir"] / filename).read_bytes()
        assert bytes_a == bytes_b, f"{filename} 两次运行结果不一致"


def test_select_stocks_global_cap_boundary_direct() -> None:
    """直接对 select_stocks_global 手工构造一个 scored 表，验证上限逻辑独立可用。

    5 只股票、3 个行业，cap=1, top_n=3：期望每行业只贡献 1 只，选到 score 最高的 3 只。
    """
    config = replace(MainlineConfig(), max_per_industry=1, regime_top_n=3)
    scored = pl.DataFrame(
        {
            "vt_symbol": ["A1.SZ", "A2.SZ", "B1.SZ", "B2.SZ", "C1.SZ"],
            "industry": ["A", "A", "B", "B", "C"],
            "score": [0.9, 0.8, 0.7, 0.6, 0.5],
            "reject_reason": [None, None, None, None, None],
        }
    )
    result = select_stocks_global(scored, config, date(2025, 6, 30))
    selected = result.filter(pl.col("selected")).get_column("vt_symbol").to_list()
    assert set(selected) == {"A1.SZ", "B1.SZ", "C1.SZ"}
