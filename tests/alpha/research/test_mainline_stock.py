"""个股层单元测试：硬过滤、三因子手算对照、打分与选取（FR-004 ~ FR-011）。"""

from dataclasses import replace
from datetime import date

import polars as pl

from vnpy.alpha.research.mainline.config import MainlineConfig
from vnpy.alpha.research.mainline.stocks import (
    apply_stock_filters,
    calc_stock_factors,
    rank_and_score,
    select_stocks,
)
from mainline_fixtures import make_business_dates, make_execution_universe, make_stock_bars

TODAY = date(2025, 6, 30)
# 008 后默认改为"简化过滤 + rs/vol 反向"，本套手算对照钉住旧的五项过滤和正向权重
CONFIG = replace(MainlineConfig(), trend_filters_enabled=True, score_weights=(0.40, 0.35, 0.25))


def make_feature_row(vt_symbol: str, **overrides) -> dict:
    """一行"全部合格"的价格特征，测试时改掉某一项来制造违规。"""
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


def run_filters(rows: list[dict], blocked_symbols: set[str] = frozenset()) -> pl.DataFrame:
    features = pl.DataFrame(rows)
    symbols = features.get_column("vt_symbol").to_list()
    universe = make_execution_universe(
        [TODAY], symbols, blocked={(TODAY, s) for s in blocked_symbols}
    )
    return apply_stock_filters(features, universe, CONFIG, TODAY)


def reason_of(result: pl.DataFrame, vt_symbol: str) -> str | None:
    return result.filter(pl.col("vt_symbol") == vt_symbol).row(0, named=True)["reject_reason"]


def test_ma_alignment_all_breakage_forms() -> None:
    """均线多头排列的四种破坏形态都要能拦住（FR-004）。"""
    result = run_filters(
        [
            make_feature_row("OK.SZ"),
            make_feature_row("BAD1.SZ", ma20=9.9),        # MA20 <= MA60
            make_feature_row("BAD2.SZ", ma60=9.4),        # MA60 <= MA120
            make_feature_row("BAD3.SZ", ma20_lagged=10.6),  # MA20 不再向上
            make_feature_row("BAD4.SZ", ma20=None),       # 均线缺数据视为不通过
        ]
    )
    assert reason_of(result, "OK.SZ") is None
    for symbol in ["BAD1.SZ", "BAD2.SZ", "BAD3.SZ", "BAD4.SZ"]:
        assert reason_of(result, symbol) == "ma_align"


def test_bias_newhigh_tradable_history_filters() -> None:
    """乖离率 / 距新高 / 可交易 / 上市时长 各自的剔除原因（FR-005/006/008）。"""
    result = run_filters(
        [
            make_feature_row("BIAS.SZ", close=13.2),          # 13.2/10.5-1 ≈ 25.7% > 25%
            make_feature_row("FAR.SZ", high_max=15.0),        # 11/15 ≈ 0.73 < 0.80
            make_feature_row("HALT.SZ"),                      # 当日不可交易
            make_feature_row("NEW.SZ", listed_bars=100),      # 上市不足 252 日
            make_feature_row("MISS.SZ", listed_bars=300),     # 不在可交易名单里 → 视同不可交易
        ],
        blocked_symbols={"HALT.SZ"},
    )
    assert reason_of(result, "BIAS.SZ") == "bias"
    assert reason_of(result, "FAR.SZ") == "nh_min"
    assert reason_of(result, "HALT.SZ") == "not_tradable"
    assert reason_of(result, "NEW.SZ") == "insufficient_history"

    # 上市时长优先级高于均线等形态问题：都不满足时报 insufficient_history
    both = run_filters([make_feature_row("BOTH.SZ", listed_bars=10, ma20=1.0)])
    assert reason_of(both, "BOTH.SZ") == "insufficient_history"


def test_factor_values_match_hand_calculation() -> None:
    """rs_60 / vol_ratio / ret_20 的数值手算对照，含量比 3 倍截断（FR-007/009）。"""
    config = replace(CONFIG, mom_window=4, confirm_window=2, vol_short=2, vol_long=4, min_listed_bars=1)
    dates = make_business_dates(date(2025, 1, 1), 5)

    # 股票每天 +2%，行业动量（快照直接给定）为 4 日 +3%
    bars = make_stock_bars("A1.SZ", dates, 0.02)
    # 构造后两天放量 4 倍：成交额 [1e6, 1e6, 1e6, 4e6, 4e6] → short 均值 4e6 / long 均值 2.5e6 = 1.6
    bars = bars.with_columns(pl.Series("turnover", [1e6, 1e6, 1e6, 4e6, 4e6]))

    filtered = pl.DataFrame(
        [make_feature_row("A1.SZ", turnover_short=4e6, turnover_long=2.5e6)]
    ).with_columns(pl.lit(None, dtype=pl.Utf8).alias("reject_reason"), pl.lit(0.9).alias("nh_252"))
    industry_snapshot = pl.DataFrame({"industry": ["GICS1A"], "mom_60": [0.03]})

    result = calc_stock_factors(filtered, bars, industry_snapshot, config, dates[-1])
    row = result.row(0, named=True)

    assert abs(row["rs_60"] - ((1.02**4 - 1) - 0.03)) < 1e-9
    assert abs(row["vol_ratio"] - 1.6) < 1e-9
    assert abs(row["ret_20"] - (1.02**2 - 1)) < 1e-9

    # 量比超过上限按 3 截断
    capped = calc_stock_factors(
        filtered.with_columns(pl.lit(9e6).alias("turnover_short")),
        bars, industry_snapshot, config, dates[-1],
    )
    assert capped.row(0, named=True)["vol_ratio"] == 3.0


def test_missing_factor_marks_stock_rejected() -> None:
    """窗口数据不全的存活个股记 missing_factor，因子值置空（Edge Case）。"""
    config = replace(CONFIG, mom_window=4, confirm_window=2, min_listed_bars=1)
    dates = make_business_dates(date(2025, 1, 1), 5)
    bars = pl.concat(
        [
            make_stock_bars("FULL.SZ", dates, 0.01),      # 市场里还有全历史的股票
            make_stock_bars("LATE.SZ", dates[2:], 0.02),  # 目标股窗口中途才上市，首日无价
        ]
    )

    filtered = pl.DataFrame([make_feature_row("LATE.SZ")]).with_columns(
        pl.lit(None, dtype=pl.Utf8).alias("reject_reason"), pl.lit(0.9).alias("nh_252")
    )
    industry_snapshot = pl.DataFrame({"industry": ["GICS1A"], "mom_60": [0.03]})

    result = calc_stock_factors(filtered, bars, industry_snapshot, config, dates[-1])
    row = result.row(0, named=True)

    assert row["reject_reason"] == "missing_factor"
    assert row["rs_60"] is None and row["vol_ratio"] is None


def make_factor_row(vt_symbol: str, rs: float, nh: float, vol: float, ret20: float, reject: str | None = None) -> dict:
    return {
        "vt_symbol": vt_symbol,
        "industry": "GICS1A",
        "rs_60": rs,
        "nh_252": nh,
        "vol_ratio": vol,
        "ret_20": ret20,
        "reject_reason": reject,
    }


def test_rank_and_score_weighted_sum_and_volume_rule() -> None:
    """百分位排名 + 加权总分手算对照；下跌个股量价项记 0.5 中性分（FR-010, R7）。"""
    factors = pl.DataFrame(
        [
            make_factor_row("S1.SZ", rs=0.30, nh=0.95, vol=2.0, ret20=0.10),   # 各项最强
            make_factor_row("S2.SZ", rs=0.10, nh=0.90, vol=1.5, ret20=0.05),
            make_factor_row("S3.SZ", rs=-0.05, nh=0.85, vol=3.0, ret20=-0.02),  # 近 20 日下跌
            make_factor_row("OUT.SZ", rs=None, nh=None, vol=None, ret20=None, reject="ma_align"),
        ]
    )
    result = rank_and_score(factors, CONFIG)
    rows = {r["vt_symbol"]: r for r in result.iter_rows(named=True)}

    # 存活 3 只：rs 排名 S1=3/3, S2=2/3, S3=1/3；nh 同序
    assert abs(rows["S1.SZ"]["rank_rs"] - 1.0) < 1e-9
    assert abs(rows["S2.SZ"]["rank_rs"] - 2 / 3) < 1e-9
    assert abs(rows["S3.SZ"]["rank_rs"] - 1 / 3) < 1e-9

    # 量价：只有上涨的 S1、S2 参与排名（S1=1.0, S2=0.5），下跌的 S3 固定 0.5
    assert abs(rows["S1.SZ"]["rank_vol"] - 1.0) < 1e-9
    assert abs(rows["S2.SZ"]["rank_vol"] - 0.5) < 1e-9
    assert abs(rows["S3.SZ"]["rank_vol"] - 0.5) < 1e-9

    # 总分 = 0.40*rs + 0.35*nh + 0.25*vol（手算 S1 = 0.40*1 + 0.35*1 + 0.25*1 = 1.0）
    assert abs(rows["S1.SZ"]["score"] - 1.0) < 1e-9
    expected_s2 = 0.40 * (2 / 3) + 0.35 * (2 / 3) + 0.25 * 0.5
    assert abs(rows["S2.SZ"]["score"] - expected_s2) < 1e-9

    # 被剔除个股不参与排名
    assert rows["OUT.SZ"]["score"] is None

    # 等权对照：权重换成 1/3 后 S2 的分数应相应变化
    equal = rank_and_score(factors, replace(CONFIG, score_weights=(1 / 3, 1 / 3, 1 / 3)))
    row_s2 = equal.filter(pl.col("vt_symbol") == "S2.SZ").row(0, named=True)
    assert abs(row_s2["score"] - (2 / 3 + 2 / 3 + 0.5) / 3) < 1e-9


def test_volatility_factor_weight_and_off_switch() -> None:
    """009 低波动率因子：负权重 = 波动越低分越高；权重 0 时列置空、总分不变。"""
    rows = [
        {**make_factor_row("CALM.SZ", rs=0.10, nh=0.90, vol=1.5, ret20=0.05), "volatility": 0.01},
        {**make_factor_row("WILD.SZ", rs=0.10, nh=0.90, vol=1.5, ret20=0.05), "volatility": 0.05},
    ]
    factors = pl.DataFrame(rows)

    # 权重 0（默认）：rank_vola 为空，两只除波动率外完全相同 → 总分一样
    off = rank_and_score(factors, CONFIG)
    assert off.get_column("rank_vola").is_null().all()
    scores = off.get_column("score").to_list()
    assert abs(scores[0] - scores[1]) < 1e-9

    # 权重 -0.25：低波的 CALM 排名 1/2、高波的 WILD 排名 2/2，
    # 总分差 = -0.25 * (1/2 - 2/2) = +0.125（CALM 高）
    on = rank_and_score(factors, replace(CONFIG, volatility_weight=-0.25))
    by_symbol = {r["vt_symbol"]: r for r in on.iter_rows(named=True)}
    assert abs(by_symbol["CALM.SZ"]["rank_vola"] - 0.5) < 1e-9
    assert abs(by_symbol["WILD.SZ"]["rank_vola"] - 1.0) < 1e-9
    assert abs((by_symbol["CALM.SZ"]["score"] - by_symbol["WILD.SZ"]["score"]) - 0.125) < 1e-9


def test_illiq_factor_weight_and_off_switch() -> None:
    """011 Amihud 非流动性因子：正权重 = 越不流动分越高；权重 0 时逐字节兼容旧行为。

    构造两只除 illiq 外完全相同的股票：
        LIQ  = 大盘白马（成交额巨大）→ illiq 数值小 → 百分位排名 1/2
        THIN = 小市值冷门（成交额小）→ illiq 数值大 → 百分位排名 2/2
    """
    rows = [
        {**make_factor_row("LIQ.SZ", rs=0.10, nh=0.90, vol=1.5, ret20=0.05), "illiq": 1e-11},
        {**make_factor_row("THIN.SZ", rs=0.10, nh=0.90, vol=1.5, ret20=0.05), "illiq": 5e-10},
    ]
    factors = pl.DataFrame(rows)

    # 权重 0（默认）：rank_illiq 为空，两只除 illiq 外完全相同 → 总分一样
    off = rank_and_score(factors, CONFIG)
    assert off.get_column("rank_illiq").is_null().all()
    scores = off.get_column("score").to_list()
    assert abs(scores[0] - scores[1]) < 1e-9

    # 权重 +0.25：THIN 排名 1.0、LIQ 排名 0.5，
    # 总分差 = +0.25 * (1.0 - 0.5) = +0.125（THIN 高）
    on = rank_and_score(factors, replace(CONFIG, illiq_weight=0.25))
    by_symbol = {r["vt_symbol"]: r for r in on.iter_rows(named=True)}
    assert abs(by_symbol["LIQ.SZ"]["rank_illiq"] - 0.5) < 1e-9
    assert abs(by_symbol["THIN.SZ"]["rank_illiq"] - 1.0) < 1e-9
    assert abs((by_symbol["THIN.SZ"]["score"] - by_symbol["LIQ.SZ"]["score"]) - 0.125) < 1e-9


def test_liquidity_filter_off_and_on() -> None:
    """011 流动性下限硬过滤：阈值 0 时全过（回归保护）；阈值 > 0 时低成交额记 low_turnover。"""
    from vnpy.alpha.research.mainline.stocks import apply_stock_filters

    dates = [TODAY]
    rows = [
        make_feature_row("BIG.SZ", turnover_short=5e8),      # 5 亿，宽松
        make_feature_row("SMALL.SZ", turnover_short=1e7),    # 1000 万，很小
    ]
    features = pl.DataFrame(rows)
    universe = make_execution_universe(dates, ["BIG.SZ", "SMALL.SZ"])

    off = apply_stock_filters(features, universe, CONFIG, TODAY)  # min=0
    assert off.filter(pl.col("vt_symbol") == "SMALL.SZ").row(0, named=True)["reject_reason"] is None
    assert off.filter(pl.col("vt_symbol") == "SMALL.SZ").row(0, named=True)["filter_liquidity"] is True

    on = apply_stock_filters(
        features, universe, replace(CONFIG, min_turnover_avg20=1e8), TODAY
    )
    assert on.filter(pl.col("vt_symbol") == "BIG.SZ").row(0, named=True)["reject_reason"] is None
    small_row = on.filter(pl.col("vt_symbol") == "SMALL.SZ").row(0, named=True)
    assert small_row["reject_reason"] == "low_turnover"
    assert small_row["filter_liquidity"] is False


def test_illiq_missing_only_gates_when_enabled() -> None:
    """illiq 缺失只在权重非 0 时触发 missing_factor（关闭时逐字节回归保护）。"""
    from vnpy.alpha.research.mainline.stocks import calc_stock_factors

    config_base = replace(CONFIG, mom_window=4, confirm_window=2, min_listed_bars=1)
    dates = make_business_dates(date(2025, 1, 1), 5)
    bars = make_stock_bars("A1.SZ", dates, 0.02)

    # illiq 列存在但为空（模拟窗口不足）
    filtered = (
        pl.DataFrame([make_feature_row("A1.SZ")])
        .with_columns(
            pl.lit(None, dtype=pl.Utf8).alias("reject_reason"),
            pl.lit(0.9).alias("nh_252"),
            pl.lit(None, dtype=pl.Float64).alias("illiq"),
        )
    )
    industry_snapshot = pl.DataFrame({"industry": ["GICS1A"], "mom_60": [0.03]})

    off = calc_stock_factors(filtered, bars, industry_snapshot, config_base, dates[-1])
    assert off.row(0, named=True)["reject_reason"] is None  # 关闭时 illiq 空不算缺失

    on = calc_stock_factors(
        filtered, bars, industry_snapshot,
        replace(config_base, illiq_weight=0.25), dates[-1],
    )
    assert on.row(0, named=True)["reject_reason"] == "missing_factor"


def test_select_top_stocks_per_industry_without_forcing_minimum() -> None:
    """每行业最多取 10 只；合格不足 6 只时有多少选多少（FR-011, AS-3）。"""
    many = [
        make_factor_row(f"M{i:02d}.SZ", rs=0.30 - i * 0.01, nh=0.95, vol=1.5, ret20=0.05)
        for i in range(12)
    ]
    few = [
        {**make_factor_row(f"F{i}.SZ", rs=0.20 - i * 0.01, nh=0.95, vol=1.5, ret20=0.05), "industry": "GICS1B"}
        for i in range(4)
    ]
    scored = rank_and_score(pl.DataFrame(many + few), CONFIG)
    result = select_stocks(scored, CONFIG, TODAY)

    picked_a = result.filter((pl.col("industry") == "GICS1A") & pl.col("selected"))
    picked_b = result.filter((pl.col("industry") == "GICS1B") & pl.col("selected"))

    assert picked_a.height == 10  # 12 只合格 → 只取前 10
    assert picked_b.height == 4   # 4 只合格 → 全部纳入，不硬凑 6 只
    # 行业内名次从 1 开始连续，且总分最高者名次为 1
    ranks = picked_a.sort("industry_rank").get_column("industry_rank").to_list()
    assert ranks == list(range(1, 11))
    best = picked_a.sort("industry_rank").row(0, named=True)
    assert best["vt_symbol"] == "M00.SZ"
