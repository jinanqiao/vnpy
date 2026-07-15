"""行业层单元测试：动量、广度手算对照 + 三道入选门槛（FR-001 ~ FR-003）。"""

from dataclasses import replace
from datetime import date

import polars as pl

from vnpy.alpha.research.mainline.config import MainlineConfig
from vnpy.alpha.research.mainline.industry import calc_industry_snapshot
from mainline_fixtures import make_business_dates, make_industry_map, make_stock_bars

# 小窗口配置：4 日动量 + 2 日确认 + 4 日广度，方便手算
SMALL = replace(
    MainlineConfig(),
    mom_window=4,
    confirm_window=2,
    breadth_window=4,
    min_industry_members=2,
    industry_rank_gate=5,
    industry_top_n=3,
    breadth_min=0.6,
)

DATES = make_business_dates(date(2025, 1, 1), 5)
LAST_DAY = DATES[-1]


def make_stock_bars_from_returns(vt_symbol: str, dates: list[date], returns: list[float]) -> pl.DataFrame:
    """按逐日收益列表生成 K 线（首日收益视为相对起始价 10 元）。"""
    closes: list[float] = []
    price = 10.0
    for r in returns:
        price = price * (1 + r)
        closes.append(price)
    return pl.DataFrame(
        {
            "datetime": dates,
            "vt_symbol": [vt_symbol] * len(dates),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "turnover": [1e6] * len(dates),
        }
    )


def test_momentum_and_breadth_match_hand_calculation() -> None:
    """A 行业一涨一平：动量 = 日均收益 5% 复利 4 天；广度 = 1/2，不到 60% 被剔除。"""
    bars = pl.concat(
        [
            make_stock_bars("A1.SZ", DATES, 0.10),
            make_stock_bars("A2.SZ", DATES, 0.0),
            make_stock_bars("B1.SZ", DATES, -0.01),
            make_stock_bars("B2.SZ", DATES, -0.01),
        ]
    )
    industry_map = make_industry_map({"GICS1A": ["A1.SZ", "A2.SZ"], "GICS1B": ["B1.SZ", "B2.SZ"]})

    snapshot = calc_industry_snapshot(bars, industry_map, SMALL, LAST_DAY)
    row_a = snapshot.filter(pl.col("industry") == "GICS1A").row(0, named=True)
    row_b = snapshot.filter(pl.col("industry") == "GICS1B").row(0, named=True)

    assert abs(row_a["mom_60"] - (1.05**4 - 1)) < 1e-9
    assert abs(row_b["mom_60"] - (0.99**4 - 1)) < 1e-9
    assert abs(row_a["breadth_60"] - 0.5) < 1e-9
    assert row_a["member_count"] == 2

    # A 动量第一且过排名门槛，但广度 0.5 < 0.6 → 剔除原因是 breadth
    assert row_a["rank_60"] == 1
    assert row_a["selected"] is False
    assert row_a["reject_reason"] == "breadth"
    assert snapshot.filter(pl.col("selected")).is_empty()


def test_broad_strong_industry_selected_without_forcing_top_n() -> None:
    """只有一个行业合格时就只选一个，不硬凑满 top_n（AS-4）。"""
    bars = pl.concat(
        [
            make_stock_bars("A1.SZ", DATES, 0.02),
            make_stock_bars("A2.SZ", DATES, 0.02),
            make_stock_bars("B1.SZ", DATES, -0.01),
            make_stock_bars("B2.SZ", DATES, -0.01),
        ]
    )
    industry_map = make_industry_map({"GICS1A": ["A1.SZ", "A2.SZ"], "GICS1B": ["B1.SZ", "B2.SZ"]})

    snapshot = calc_industry_snapshot(bars, industry_map, SMALL, LAST_DAY)
    selected = snapshot.filter(pl.col("selected")).get_column("industry").to_list()

    assert selected == ["GICS1A"]
    assert snapshot.filter(pl.col("industry") == "GICS1A").row(0, named=True)["breadth_60"] == 1.0


def test_dual_window_gate_rejects_fading_momentum() -> None:
    """60 日强但 20 日转弱的行业，过不了双窗口排名门槛（AS-2）。"""
    config = replace(SMALL, mom_window=6, confirm_window=2, industry_rank_gate=1, breadth_min=0.0)
    dates = make_business_dates(date(2025, 1, 1), 7)

    # A：前 4 天每天 +5%，后 2 天走平（整段最强，近期熄火）
    # B：前 4 天走平，后 2 天每天 +3%（近期最强，整段次之）
    returns_a = [0.05, 0.05, 0.05, 0.05, 0.0, 0.0, 0.0]
    returns_b = [0.0, 0.0, 0.0, 0.0, 0.0, 0.03, 0.03]
    bars = pl.concat(
        [
            make_stock_bars_from_returns("A1.SZ", dates, returns_a),
            make_stock_bars_from_returns("A2.SZ", dates, returns_a),
            make_stock_bars_from_returns("B1.SZ", dates, returns_b),
            make_stock_bars_from_returns("B2.SZ", dates, returns_b),
        ]
    )
    industry_map = make_industry_map({"GICS1A": ["A1.SZ", "A2.SZ"], "GICS1B": ["B1.SZ", "B2.SZ"]})

    snapshot = calc_industry_snapshot(bars, industry_map, config, dates[-1])
    row_a = snapshot.filter(pl.col("industry") == "GICS1A").row(0, named=True)
    row_b = snapshot.filter(pl.col("industry") == "GICS1B").row(0, named=True)

    assert row_a["rank_60"] == 1 and row_a["rank_20"] == 2
    assert row_b["rank_60"] == 2 and row_b["rank_20"] == 1
    assert row_a["reject_reason"] == "rank_gate"
    assert row_b["reject_reason"] == "rank_gate"
    assert snapshot.filter(pl.col("selected")).is_empty()


def test_small_industry_excluded_from_ranking() -> None:
    """成分股不足 min_industry_members 的行业不参与排名，原因记 member_count。"""
    bars = pl.concat(
        [
            make_stock_bars("A1.SZ", DATES, 0.10),
            make_stock_bars("B1.SZ", DATES, 0.01),
            make_stock_bars("B2.SZ", DATES, 0.01),
        ]
    )
    industry_map = make_industry_map({"GICS1A": ["A1.SZ"], "GICS1B": ["B1.SZ", "B2.SZ"]})

    snapshot = calc_industry_snapshot(bars, industry_map, SMALL, LAST_DAY)
    row_a = snapshot.filter(pl.col("industry") == "GICS1A").row(0, named=True)

    assert row_a["reject_reason"] == "member_count"
    assert row_a["rank_60"] is None
    # B 行业不受影响，正常入选
    assert snapshot.filter(pl.col("selected")).get_column("industry").to_list() == ["GICS1B"]


def test_insufficient_history_returns_empty_snapshot() -> None:
    """历史交易日不足 mom_window+1 时返回空快照，由流水线记录后跳过。"""
    short_dates = make_business_dates(date(2025, 1, 1), 3)
    bars = make_stock_bars("A1.SZ", short_dates, 0.01)
    industry_map = make_industry_map({"GICS1A": ["A1.SZ"]})

    snapshot = calc_industry_snapshot(bars, industry_map, SMALL, short_dates[-1])

    assert snapshot.is_empty()
