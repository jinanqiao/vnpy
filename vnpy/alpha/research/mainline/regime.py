"""regime 模式：行业层降级为"市场状态开关"，个股直接在全市场选。

这个文件回答一个问题：如果行业动量选不准行业（见 outputs/factor_checks/ 的
Rank IC 检验），行业层还剩什么用——答案是"有没有主线"这个状态本身。

规则（与 select 模式的差异）：
    1. 行业层照常算三关（动量双排名 + 广度），但只用来回答"该期建不建仓"：
       有主线行业 → 建仓；没有 → 空清单（回测层的无信号清仓会接住这个信息）；
    2. 建仓时不再限定主线行业内选股，而是把全市场（有行情的全部股票）
       作为候选池，五项硬过滤和三因子打分照旧；
    3. 相对强度因子的基准从"所属行业动量"换成"全市场等权动量"——
       没有行业层之后，剥离的是大盘顺风车而不是行业顺风车；
    4. 全池按总分取前 regime_top_n 只（默认 30，对齐 select 模式 3 行业 x 10 只）。

对外只需要调用 calc_stock_snapshot_regime()，返回结构与 select 模式一致的快照。
"""

from __future__ import annotations

from datetime import date

import polars as pl

from .config import MainlineConfig
from .industry import industry_momentum, stock_returns_in_window, window_trade_dates
from .stocks import (
    STOCK_SNAPSHOT_COLUMNS,
    apply_stock_filters,
    calc_price_features,
    calc_stock_factors,
    rank_and_score,
)

MARKET_LABEL = "全市场"


def market_momentum(bars: pl.DataFrame, config: MainlineConfig, rebalance_date: date) -> float | None:
    """全市场等权动量：所有股票等权日收益复合的主窗口涨幅（regime 模式的 rs 基准）。

    历史不足以覆盖主窗口时返回 None，由调用方按"窗口不足"跳过该期。
    """
    dates_main = window_trade_dates(bars, rebalance_date, config.mom_window)
    if not dates_main:
        return None
    returns = stock_returns_in_window(bars, dates_main)
    market_map = returns.select("vt_symbol").unique().with_columns(pl.lit(MARKET_LABEL).alias("industry"))
    nav = industry_momentum(returns, market_map)
    if nav.is_empty():
        return None
    return float(nav.get_column("momentum")[0])


def select_stocks_global(scored: pl.DataFrame, config: MainlineConfig, rebalance_date: date) -> pl.DataFrame:
    """全池统一排名取前 regime_top_n（与 select 模式的"每行业取前 N"对应）。

    同分按代码字典序，保证结果可复现。industry_rank 列在 regime 模式下
    存的是全池名次（列名沿用快照 schema，便于两种模式共用产物结构）。

    010 行业上限（max_per_industry > 0）：先按行业内 score 排前 max_per_industry 只
    形成候选池，再从候选池按全局 score 取前 regime_top_n；行业霸榜时超限个股的名额
    自动顺延给全局排名下一位的其他行业股票。0 时逐字节等价于旧行为。
    """
    survived = pl.col("reject_reason").is_null()
    scored = scored.sort(["score", "vt_symbol"], descending=[True, False])
    scored = scored.with_columns(
        pl.when(survived)
        .then(pl.col("score").rank(method="ordinal", descending=True))
        .otherwise(None)
        .cast(pl.Int32)
        .alias("industry_rank")
    )

    if config.max_per_industry <= 0:
        # 兼容路径：不做行业上限，逐字节保持 008 后行为
        return scored.with_columns(
            (pl.col("industry_rank") <= config.regime_top_n).fill_null(False).alias("selected"),
            pl.lit(rebalance_date).alias("rebalance_date"),
        )

    # 行业上限路径：先算存活股在行业内的 score 名次（同分按 vt_symbol 字典序），
    # 行业内名次 <= max_per_industry 的进入候选池；候选池内再按全局 score 排序
    # 取前 regime_top_n（用 cum_sum 而不是 rank 是为了跳过非候选行——rank 会把
    # 候选池外的行也占用名次，导致真正入选者拿到膨胀后的名次）。
    intra_industry_rank = (
        pl.when(survived)
        .then(pl.col("score").rank(method="ordinal", descending=True).over("industry"))
        .otherwise(None)
        .cast(pl.Int32)
    )
    scored = scored.with_columns(intra_industry_rank.alias("_intra_industry_rank"))
    eligible = pl.col("_intra_industry_rank") <= config.max_per_industry

    eligible_rank = (
        pl.when(survived & eligible).then(1).otherwise(0).cum_sum()
    )
    scored = scored.with_columns(
        pl.when(survived & eligible)
        .then(eligible_rank)
        .otherwise(None)
        .cast(pl.Int32)
        .alias("_eligible_rank")
    )
    return scored.with_columns(
        (pl.col("_eligible_rank") <= config.regime_top_n).fill_null(False).alias("selected"),
        pl.lit(rebalance_date).alias("rebalance_date"),
    ).drop(["_intra_industry_rank", "_eligible_rank"])


def calc_stock_snapshot_regime(
    bars: pl.DataFrame,
    industry_map: pl.DataFrame,
    execution_universe: pl.DataFrame,
    config: MainlineConfig,
    rebalance_date: date,
) -> pl.DataFrame:
    """regime 模式的个股层总入口：全市场候选池 → 过滤 → 打分 → 全池取前 N。

    行业标签仅用于审计（缺失记"未知"），不参与排名分组。
    返回快照的列结构与 select 模式完全一致。
    """
    mom = market_momentum(bars, config, rebalance_date)
    if mom is None:
        return pl.DataFrame(schema=STOCK_SNAPSHOT_COLUMNS)

    features = calc_price_features(bars, config, rebalance_date)
    features = features.join(industry_map, on="vt_symbol", how="left").with_columns(
        pl.col("industry").fill_null("未知")
    )

    filtered = apply_stock_filters(features, execution_universe, config, rebalance_date)

    # 复用 select 模式的因子计算：把"行业动量"整体替换成全市场动量，
    # rs = 个股累计收益 - 全市场等权动量。
    pseudo_snapshot = (
        filtered.select("industry").unique().with_columns(pl.lit(mom).alias("mom_60"))
    )
    factors = calc_stock_factors(filtered, bars, pseudo_snapshot, config, rebalance_date)
    scored = rank_and_score(factors, config)
    snapshot = select_stocks_global(scored, config, rebalance_date)

    return snapshot.select(list(STOCK_SNAPSHOT_COLUMNS.keys())).sort(["industry", "vt_symbol"])
