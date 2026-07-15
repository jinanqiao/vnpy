"""S16 剔除高波股：max_volatility 硬过滤开关的两项测试。

- 默认 max_volatility=0.0：filter_volatility 恒 True、reject_reason 不出现 high_volatility
  → 逐字节回归旧行为的保护测试；
- max_volatility=0.05 时，波动率 0.06 的股票 → reject_reason='high_volatility'。
"""

from dataclasses import replace
from datetime import date

import polars as pl

from vnpy.alpha.research.mainline.config import MainlineConfig
from vnpy.alpha.research.mainline.stocks import (
    REJECT_REASONS,
    STOCK_SNAPSHOT_COLUMNS,
    apply_stock_filters,
)
from mainline_fixtures import make_execution_universe

TODAY = date(2025, 6, 30)
# 与 test_mainline_stock 保持一致：钉住旧五项过滤 + 正向权重的基线
CONFIG = replace(MainlineConfig(), trend_filters_enabled=True, score_weights=(0.40, 0.35, 0.25))


def make_feature_row(vt_symbol: str, **overrides) -> dict:
    """一行"全部合格"的价格特征（含 volatility 列）。"""
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
        "volatility": 0.02,
    }
    row.update(overrides)
    return row


def test_snapshot_column_and_enum_wired() -> None:
    """S16 相关列/枚举都要接进公共契约里（防止漏改。"""
    assert "filter_volatility" in STOCK_SNAPSHOT_COLUMNS
    assert "high_volatility" in REJECT_REASONS


def test_max_volatility_off_keeps_all_survivors() -> None:
    """max_volatility=0（默认）时，无论波动率多大都不该被 high_volatility 拦。

    这是"逐字节回归"保护：关闭开关时，filter_volatility 恒 True、reject_reason 与旧行为一致。
    """
    features = pl.DataFrame(
        [
            make_feature_row("CALM.SZ", volatility=0.01),
            make_feature_row("WILD.SZ", volatility=0.06),  # 高波
            make_feature_row("NAN.SZ", volatility=None),   # 波动率缺失也不该被拦
        ]
    )
    universe = make_execution_universe(
        [TODAY], ["CALM.SZ", "WILD.SZ", "NAN.SZ"]
    )
    result = apply_stock_filters(features, universe, CONFIG, TODAY)
    for vt in ["CALM.SZ", "WILD.SZ", "NAN.SZ"]:
        row = result.filter(pl.col("vt_symbol") == vt).row(0, named=True)
        assert row["filter_volatility"] is True, f"{vt} filter_volatility 应恒 True"
        assert row["reject_reason"] is None, f"{vt} 不应被高波拦下（当前 {row['reject_reason']}）"


def test_max_volatility_on_rejects_high_vol() -> None:
    """max_volatility=0.05 时，波动率 0.06 的高波股 → reject_reason='high_volatility'。

    同时验证：低波（0.01）继续通过；波动率缺失（None）视为不通过（保守拦下，防止漏网之鱼）。
    """
    features = pl.DataFrame(
        [
            make_feature_row("CALM.SZ", volatility=0.01),
            make_feature_row("WILD.SZ", volatility=0.06),
            make_feature_row("NAN.SZ", volatility=None),
        ]
    )
    universe = make_execution_universe(
        [TODAY], ["CALM.SZ", "WILD.SZ", "NAN.SZ"]
    )
    config_on = replace(CONFIG, max_volatility=0.05)
    result = apply_stock_filters(features, universe, config_on, TODAY)

    calm = result.filter(pl.col("vt_symbol") == "CALM.SZ").row(0, named=True)
    wild = result.filter(pl.col("vt_symbol") == "WILD.SZ").row(0, named=True)
    nan = result.filter(pl.col("vt_symbol") == "NAN.SZ").row(0, named=True)

    assert calm["filter_volatility"] is True and calm["reject_reason"] is None
    assert wild["filter_volatility"] is False
    assert wild["reject_reason"] == "high_volatility"
    # 缺失也被拦：与 filter_liquidity 的 fill_null(False) 行为一致
    assert nan["filter_volatility"] is False
    assert nan["reject_reason"] == "high_volatility"
