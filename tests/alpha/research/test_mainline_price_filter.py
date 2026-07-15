"""S12 低价硬过滤单元测试：默认关闭时逐字节回归，启用后低价股记 low_price。

这些测试钉住 012 的两个契约：
    1. min_price=0（默认）时 filter_price 恒为 True，reject_reason 不变，
       与不加过滤的旧行为逐字节一致；
    2. min_price=3 时收盘价 2.5 元的股票 reject_reason 记 "low_price"，
       filter_price 为 False。

参考模板：test_mainline_stock.py::test_liquidity_filter_off_and_on。
"""

from dataclasses import replace
from datetime import date

import polars as pl

from vnpy.alpha.research.mainline.config import MainlineConfig
from vnpy.alpha.research.mainline.stocks import apply_stock_filters, REJECT_REASONS
from mainline_fixtures import make_execution_universe

TODAY = date(2025, 6, 30)
# 与 test_mainline_stock 保持一致：钉住五项过滤 + 正向权重的旧参数
CONFIG = replace(MainlineConfig(), trend_filters_enabled=True, score_weights=(0.40, 0.35, 0.25))


def make_feature_row(vt_symbol: str, close: float = 11.0, **overrides) -> dict:
    """全部合格的价格特征骨架；测试里改 close 造低价股。"""
    row = {
        "vt_symbol": vt_symbol,
        "industry": "GICS1A",
        "listed_bars": 300,
        "close": close,
        "ma20": close * 0.95,
        "ma60": close * 0.90,
        "ma120": close * 0.85,
        "ma20_lagged": close * 0.92,
        "high_max": close * 1.10,
        "turnover_short": 1.5e6,
        "turnover_long": 1.0e6,
    }
    row.update(overrides)
    return row


def test_price_filter_off_by_default_and_reject_reason_reasons_include_low_price() -> None:
    """默认 min_price=0：filter_price 恒 True，与旧行为逐字节兼容；枚举里已登记 low_price。"""
    assert "low_price" in REJECT_REASONS

    features = pl.DataFrame(
        [
            make_feature_row("A.SZ", close=15.0),
            make_feature_row("B.SZ", close=2.5),  # 低价，但默认关闭时不该被剔
        ]
    )
    universe = make_execution_universe([TODAY], ["A.SZ", "B.SZ"])

    result = apply_stock_filters(features, universe, CONFIG, TODAY)
    assert CONFIG.min_price == 0.0
    # 两只 filter_price 均为 True（关闭时恒真，逐字节回归）
    assert result.get_column("filter_price").to_list() == [True, True]
    # 低价那只不应因价格被剔（其它五项都合格 → reject_reason 为空）
    b_row = result.filter(pl.col("vt_symbol") == "B.SZ").row(0, named=True)
    assert b_row["reject_reason"] is None


def test_price_filter_on_marks_low_price_stock_as_low_price() -> None:
    """min_price=3：收盘价 2.5 元 < 3 应记 low_price；≥ 3 元的股票不受影响。"""
    config = replace(CONFIG, min_price=3.0)

    features = pl.DataFrame(
        [
            make_feature_row("HIGH.SZ", close=15.0),  # 高价，通过
            make_feature_row("LOW.SZ", close=2.5),    # 2.5 < 3.0，剔除
            make_feature_row("EDGE.SZ", close=3.0),   # 3.0 == 阈值，通过（>=）
        ]
    )
    universe = make_execution_universe([TODAY], ["HIGH.SZ", "LOW.SZ", "EDGE.SZ"])

    result = apply_stock_filters(features, universe, config, TODAY)
    rows = {r["vt_symbol"]: r for r in result.iter_rows(named=True)}

    assert rows["HIGH.SZ"]["filter_price"] is True
    assert rows["HIGH.SZ"]["reject_reason"] is None

    assert rows["LOW.SZ"]["filter_price"] is False
    assert rows["LOW.SZ"]["reject_reason"] == "low_price"

    assert rows["EDGE.SZ"]["filter_price"] is True
    assert rows["EDGE.SZ"]["reject_reason"] is None


def test_config_rejects_negative_min_price() -> None:
    """min_price 允许 0（关闭）与正数，负数直接报错。"""
    replace(MainlineConfig(), min_price=0.0)   # OK
    replace(MainlineConfig(), min_price=3.0)   # OK
    try:
        replace(MainlineConfig(), min_price=-1.0)
    except ValueError as exc:
        assert "min_price" in str(exc)
    else:
        raise AssertionError("min_price=-1.0 应当抛 ValueError")
