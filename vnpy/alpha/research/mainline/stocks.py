"""个股层：在主线行业内先过滤、后打分，选出强势个股。

这个文件回答一个问题：主线行业里，具体买哪些股票。

流程分三步（对应 spec 的 FR-004 ~ FR-011）：
    1. 硬过滤（一票否决）：上市满一年、当日可交易两项必过；
       趋势三关（均线多头 / 乖离 / 距新高）由 trend_filters_enabled 控制，
       008 起默认关闭（池级检验显示趋势过滤后的池子跑输全市场）；
    2. 三因子打分：相对强度、距 52 周新高、量价配合，各自转百分位排名后
       按 score_weights 加权合成总分——权重允许为负（= 因子反向使用），
       008 起默认 (-0.40, +0.35, -0.25)：rs 与 vol 反向、nh 正向；
    3. 每个主线行业按总分取前几名（默认最多 10 只，不足 6 只时有多少要多少）；
       industry_mode=none/regime 时改为全池取前 regime_top_n（见 regime.py）。

对外只需要调用 calc_stock_snapshot()，返回"个股信号快照"和"入选清单"两张表。
"""

from __future__ import annotations

from datetime import date

import polars as pl

from .config import MainlineConfig
from .industry import stock_returns_in_window, window_trade_dates

# 个股信号快照的固定列结构（对照 specs 的 data-model.md）
STOCK_SNAPSHOT_COLUMNS = {
    "rebalance_date": pl.Date,
    "vt_symbol": pl.Utf8,
    "industry": pl.Utf8,
    "filter_history": pl.Boolean,
    "filter_tradable": pl.Boolean,
    "filter_ma_align": pl.Boolean,
    "filter_bias": pl.Boolean,
    "filter_nh": pl.Boolean,
    "filter_liquidity": pl.Boolean,
    "filter_price": pl.Boolean,
    "filter_volatility": pl.Boolean,
    "reject_reason": pl.Utf8,
    "rs_60": pl.Float64,
    "nh_252": pl.Float64,
    "vol_ratio": pl.Float64,
    "volatility": pl.Float64,
    "illiq": pl.Float64,
    "rank_rs": pl.Float64,
    "rank_nh": pl.Float64,
    "rank_vol": pl.Float64,
    "rank_vola": pl.Float64,
    "rank_illiq": pl.Float64,
    "score": pl.Float64,
    "industry_rank": pl.Int32,
    "selected": pl.Boolean,
}

# 剔除原因的固定枚举（对照 specs 的 contracts/artifacts-schema.md）
REJECT_REASONS = [
    "insufficient_history",  # 上市不满 252 个交易日，因子窗口算不全
    "not_tradable",          # 调仓日停牌 / ST / 一字涨停，买不进
    "ma_align",              # 均线不是多头排列，趋势结构已破坏
    "bias",                  # 乖离率过大，短期涨得太陡
    "nh_min",                # 离 52 周新高太远，趋势不成立
    "low_turnover",          # 近 20 日均成交额低于阈值（容量过滤，默认关闭）
    "low_price",             # 收盘价低于 min_price（012 低价过滤，默认关闭）
    "high_volatility",       # 近 N 日日收益 std 高于 max_volatility（S16 过热过滤，默认关闭）
    "missing_factor",        # 窗口内数据缺失，因子算不出来
]


def calc_price_features(bars: pl.DataFrame, config: MainlineConfig, rebalance_date: date) -> pl.DataFrame:
    """按"每只股票自己的 K 线序列"计算过滤所需的价格特征。

    窗口按 bar 数量取（例如 MA20 = 最近 20 根 K 线的均价），停牌日自然跳过。
    返回每只股票一行: vt_symbol, listed_bars, close, ma20, ma60, ma120,
    ma20_lagged（5 根 K 线之前的 MA20）, high_max（52 周最高价）。
    """
    w_short, w_mid, w_long = config.ma_windows
    history = bars.filter(pl.col("datetime") <= rebalance_date).sort(["vt_symbol", "datetime"])

    # 011 Amihud 非流动性：逐日 |日收益| / 成交额，成交额<=0（停牌）先置空
    # 让下面的 mean 自动跳过；tail 取到样本不足会返回 null，missing_factor 保护。
    daily_illiq = (
        pl.col("close").pct_change().abs()
        / pl.when(pl.col("turnover") > 0).then(pl.col("turnover")).otherwise(None)
    )

    features = history.group_by("vt_symbol").agg(
        pl.len().cast(pl.Int32).alias("listed_bars"),
        pl.col("close").last().alias("close"),
        pl.col("close").tail(w_short).mean().alias("ma20"),
        pl.col("close").tail(w_mid).mean().alias("ma60"),
        pl.col("close").tail(w_long).mean().alias("ma120"),
        # 5 根 K 线之前的 MA20：先把序列整体后移 5 位再取尾部 20 个的均值
        pl.col("close").shift(config.ma_slope_lag).tail(w_short).mean().alias("ma20_lagged"),
        pl.col("high").tail(config.nh_window).max().alias("high_max"),
        pl.col("turnover").tail(config.vol_short).mean().alias("turnover_short"),
        pl.col("turnover").tail(config.vol_long).mean().alias("turnover_long"),
        # 009 低波动率因子：近 N 日日收益率标准差（停牌日自然跳过）
        pl.col("close").pct_change().tail(config.volatility_window).std().alias("volatility"),
        # 011 非流动性因子：近 N 日 mean(|日收益| / 成交额)（停牌日自然跳过）
        daily_illiq.tail(config.illiq_window).mean().alias("illiq"),
    )
    return features


def apply_stock_filters(
    features: pl.DataFrame,
    execution_universe: pl.DataFrame,
    config: MainlineConfig,
    rebalance_date: date,
) -> pl.DataFrame:
    """执行五项硬过滤，记录每项是否通过和第一个不通过的原因（FR-004 ~ FR-006, FR-008）。"""
    tradable_today = (
        execution_universe.filter(pl.col("datetime") == rebalance_date)
        .select("vt_symbol", pl.col("in_execution").alias("filter_tradable"))
    )
    result = features.join(tradable_today, on="vt_symbol", how="left")
    result = result.with_columns(pl.col("filter_tradable").fill_null(False))

    result = result.with_columns(
        (pl.col("listed_bars") >= config.min_listed_bars).alias("filter_history"),
        (
            (pl.col("ma20") > pl.col("ma60"))
            & (pl.col("ma60") > pl.col("ma120"))
            & (pl.col("ma20") > pl.col("ma20_lagged"))
        ).fill_null(False).alias("filter_ma_align"),
        ((pl.col("close") / pl.col("ma20") - 1) <= config.bias_max).fill_null(False).alias("filter_bias"),
        (pl.col("close") / pl.col("high_max")).alias("nh_252"),
    )
    result = result.with_columns(
        (pl.col("nh_252") >= config.nh_min).fill_null(False).alias("filter_nh"),
    )

    # 011 流动性硬过滤：turnover_short（近 20 日均成交额）低于阈值直接剔除。
    # 阈值 0 = 不启用（默认），此时 filter_liquidity 恒为 True，逐字节兼容旧行为。
    if config.min_turnover_avg20 > 0:
        result = result.with_columns(
            (pl.col("turnover_short") >= config.min_turnover_avg20)
            .fill_null(False).alias("filter_liquidity"),
        )
    else:
        result = result.with_columns(pl.lit(True).alias("filter_liquidity"))

    # 012 低价硬过滤：收盘价 < min_price 直接剔除（避开壳股/低质小盘）。
    # 阈值 0 = 不启用（默认），此时 filter_price 恒为 True，逐字节兼容旧行为。
    if config.min_price > 0:
        result = result.with_columns(
            (pl.col("close") >= config.min_price)
            .fill_null(False).alias("filter_price"),
        )
    else:
        result = result.with_columns(pl.lit(True).alias("filter_price"))

    # S16 过热过滤：近 volatility_window 日日收益 std > max_volatility 直接剔除。
    # 阈值 0 = 不启用（默认），filter_volatility 恒 True，逐字节兼容旧行为。
    # 只做上限过滤（不进入打分），避免像 011 那样把候选池整体拉向小盘/低波区域。
    if config.max_volatility > 0:
        if "volatility" not in result.columns:
            result = result.with_columns(pl.lit(None, dtype=pl.Float64).alias("volatility"))
        result = result.with_columns(
            (pl.col("volatility") <= config.max_volatility)
            .fill_null(False).alias("filter_volatility"),
        )
    else:
        result = result.with_columns(pl.lit(True).alias("filter_volatility"))

    # 008 简化过滤：趋势三关（均线多头/乖离/距新高）关闭时一律视为通过，
    # 只保留"上市满一年 + 当日可交易"两项必要过滤（nh_252 仍照常计算，打分要用）。
    if not config.trend_filters_enabled:
        result = result.with_columns(
            pl.lit(True).alias("filter_ma_align"),
            pl.lit(True).alias("filter_bias"),
            pl.lit(True).alias("filter_nh"),
        )

    result = result.with_columns(
        pl.when(~pl.col("filter_history"))
        .then(pl.lit("insufficient_history"))
        .when(~pl.col("filter_tradable"))
        .then(pl.lit("not_tradable"))
        .when(~pl.col("filter_ma_align"))
        .then(pl.lit("ma_align"))
        .when(~pl.col("filter_bias"))
        .then(pl.lit("bias"))
        .when(~pl.col("filter_nh"))
        .then(pl.lit("nh_min"))
        .when(~pl.col("filter_liquidity"))
        .then(pl.lit("low_turnover"))
        .when(~pl.col("filter_price"))
        .then(pl.lit("low_price"))
        .when(~pl.col("filter_volatility"))
        .then(pl.lit("high_volatility"))
        .otherwise(None)
        .alias("reject_reason")
    )
    return result


def calc_stock_factors(
    filtered: pl.DataFrame,
    bars: pl.DataFrame,
    industry_snapshot: pl.DataFrame,
    config: MainlineConfig,
    rebalance_date: date,
) -> pl.DataFrame:
    """计算三个打分因子的原始值（FR-007 ~ FR-009）。

    相对强度 rs_60  = 个股主窗口累计收益 - 所属行业主窗口动量（剥离行业顺风车，
                      窗口 = config.mom_window，当前默认 20 日，列名沿用历史命名）；
    距新高   nh_252 = 收盘价 / 52 周最高价（过滤阶段已算好）；
    量价     vol_ratio = 近 20 日日均成交额 / 近 60 日日均成交额，超过 3 按 3 算。
    另算 ret_20（确认窗口累计收益，默认 5 日），打分时用来判断"放量"是伴随上涨还是下跌。
    窗口数据缺失导致因子算不出来的存活个股，原因记为 missing_factor。
    """
    dates_60 = window_trade_dates(bars, rebalance_date, config.mom_window)
    dates_20 = window_trade_dates(bars, rebalance_date, config.confirm_window)

    cum_60 = (
        stock_returns_in_window(bars, dates_60)
        .group_by("vt_symbol")
        .agg(pl.col("running_return").last().alias("cum_60"))
    )
    cum_20 = (
        stock_returns_in_window(bars, dates_20)
        .group_by("vt_symbol")
        .agg(pl.col("running_return").last().alias("ret_20"))
    )
    industry_mom = industry_snapshot.select("industry", pl.col("mom_60").alias("industry_mom_60"))

    result = (
        filtered.join(cum_60, on="vt_symbol", how="left")
        .join(cum_20, on="vt_symbol", how="left")
        .join(industry_mom, on="industry", how="left")
    )
    if "volatility" not in result.columns:  # 手工构造的特征表可能没有该列（测试/旧调用方）
        result = result.with_columns(pl.lit(None, dtype=pl.Float64).alias("volatility"))
    if "illiq" not in result.columns:  # 手工构造的特征表可能没有该列（测试/旧调用方）
        result = result.with_columns(pl.lit(None, dtype=pl.Float64).alias("illiq"))
    result = result.with_columns(
        (pl.col("cum_60") - pl.col("industry_mom_60")).alias("rs_60"),
        (pl.col("turnover_short") / pl.col("turnover_long")).clip(upper_bound=config.vol_cap).alias("vol_ratio"),
    )

    # 存活个股若任一因子缺失 → missing_factor（不允许用默认值补位）
    # 可选因子（低波动率 / Amihud）只在启用（权重非 0）时参与缺失判定，
    # 关闭时保持与旧行为逐字节兼容。
    factor_missing = (
        pl.col("rs_60").is_null() | pl.col("nh_252").is_null()
        | pl.col("vol_ratio").is_null() | pl.col("ret_20").is_null()
    )
    if config.volatility_weight != 0.0:
        factor_missing = factor_missing | pl.col("volatility").is_null()
    if config.illiq_weight != 0.0:
        factor_missing = factor_missing | pl.col("illiq").is_null()
    result = result.with_columns(
        pl.when(pl.col("reject_reason").is_null() & factor_missing)
        .then(pl.lit("missing_factor"))
        .otherwise(pl.col("reject_reason"))
        .alias("reject_reason")
    )

    # 被任何原因剔除的个股，因子值统一置空（快照里一眼看出它没参与打分）
    survived = pl.col("reject_reason").is_null()
    result = result.with_columns(
        pl.when(survived).then(pl.col("rs_60")).otherwise(None).alias("rs_60"),
        pl.when(survived).then(pl.col("nh_252")).otherwise(None).alias("nh_252"),
        pl.when(survived).then(pl.col("vol_ratio")).otherwise(None).alias("vol_ratio"),
        pl.when(survived).then(pl.col("volatility")).otherwise(None).alias("volatility"),
        pl.when(survived).then(pl.col("illiq")).otherwise(None).alias("illiq"),
        pl.when(survived).then(pl.col("ret_20")).otherwise(None).alias("ret_20"),
    )
    return result


def percentile_rank(column_name: str) -> pl.Expr:
    """把一列数值转成 (0, 1] 的百分位排名，1 = 全池最强（并列取平均名次）。"""
    return pl.col(column_name).rank(method="average") / pl.col(column_name).count()


def rank_and_score(factors: pl.DataFrame, config: MainlineConfig) -> pl.DataFrame:
    """把因子原始值转成百分位排名并加权合成总分（FR-010）。

    百分位排名在"全部主线行业的存活个股"这个候选池内进行，取值 (0, 1]，越大越强。
    量价因子的特殊规则（R7）：只有近 20 日上涨的股票才参与量比排名（放量上涨才是确认），
    近 20 日下跌的股票该项直接记 0.5（中性分），不给"放量下跌"加分——
    实现上先把上涨/下跌两组分开，各自处理后再拼回，保证下跌组不占用排名位次。
    权重允许为负：负权重等价于该因子反向使用（排名越低总分越高），
    008 起 rs / vol 默认反向（近 5 年 Rank IC 为负，短期反转效应）。
    """
    survived = pl.col("reject_reason").is_null()
    survivors = factors.filter(survived)
    rest = factors.filter(~survived)

    survivors = survivors.with_columns(
        percentile_rank("rs_60").alias("rank_rs"),
        percentile_rank("nh_252").alias("rank_nh"),
    )

    rising = pl.col("ret_20") > 0
    rising_part = survivors.filter(rising).with_columns(percentile_rank("vol_ratio").alias("rank_vol"))
    flat_part = survivors.filter(~rising).with_columns(pl.lit(0.5).alias("rank_vol"))
    survivors = pl.concat([rising_part, flat_part])

    # 009 可选第四因子：低波动率。权重为 0 时不排名（列置空），行为与 008 完全一致。
    if config.volatility_weight != 0.0:
        survivors = survivors.with_columns(percentile_rank("volatility").alias("rank_vola"))
    else:
        survivors = survivors.with_columns(pl.lit(None, dtype=pl.Float64).alias("rank_vola"))

    # 011 可选第五因子：Amihud 非流动性。权重为 0 时不排名（列置空），逐字节回归。
    if config.illiq_weight != 0.0:
        survivors = survivors.with_columns(percentile_rank("illiq").alias("rank_illiq"))
    else:
        survivors = survivors.with_columns(pl.lit(None, dtype=pl.Float64).alias("rank_illiq"))

    w_rs, w_nh, w_vol = config.score_weights
    score = w_rs * pl.col("rank_rs") + w_nh * pl.col("rank_nh") + w_vol * pl.col("rank_vol")
    if config.volatility_weight != 0.0:
        score = score + config.volatility_weight * pl.col("rank_vola")
    if config.illiq_weight != 0.0:
        score = score + config.illiq_weight * pl.col("rank_illiq")
    survivors = survivors.with_columns(score.alias("score"))

    rest = rest.with_columns(
        pl.lit(None, dtype=pl.Float64).alias("rank_rs"),
        pl.lit(None, dtype=pl.Float64).alias("rank_nh"),
        pl.lit(None, dtype=pl.Float64).alias("rank_vol"),
        pl.lit(None, dtype=pl.Float64).alias("rank_vola"),
        pl.lit(None, dtype=pl.Float64).alias("rank_illiq"),
        pl.lit(None, dtype=pl.Float64).alias("score"),
    )
    return pl.concat([survivors, rest])


def select_stocks(scored: pl.DataFrame, config: MainlineConfig, rebalance_date: date) -> pl.DataFrame:
    """每个主线行业按总分取前几名，生成最终的个股信号快照（FR-011）。

    行业内名次 industry_rank 按总分从高到低（同分按代码字典序，保证结果可复现），
    名次 <= stocks_per_industry_max（默认 10）的入选。
    不足 stocks_per_industry_min（默认 6）只时有多少选多少，由流水线记录数量不足。
    """
    survived = pl.col("reject_reason").is_null()
    scored = scored.sort(["industry", "score", "vt_symbol"], descending=[False, True, False])
    scored = scored.with_columns(
        pl.when(survived)
        .then(pl.col("score").rank(method="ordinal", descending=True).over("industry"))
        .otherwise(None)
        .cast(pl.Int32)
        .alias("industry_rank")
    )
    scored = scored.with_columns(
        (pl.col("industry_rank") <= config.stocks_per_industry_max).fill_null(False).alias("selected"),
        pl.lit(rebalance_date).alias("rebalance_date"),
    )
    return scored


def calc_stock_snapshot(
    bars: pl.DataFrame,
    industry_map: pl.DataFrame,
    execution_universe: pl.DataFrame,
    industry_snapshot: pl.DataFrame,
    config: MainlineConfig,
    rebalance_date: date,
) -> pl.DataFrame:
    """个股层总入口：给定当期主线行业，产出完整的个股信号快照。

    快照包含主线行业内的全部个股（含被剔除者），列结构对照 data-model.md。
    """
    mainline = industry_snapshot.filter(pl.col("selected")).get_column("industry").to_list()
    members = industry_map.filter(pl.col("industry").is_in(mainline))

    features = calc_price_features(bars, config, rebalance_date)
    features = members.join(features, on="vt_symbol", how="inner")

    filtered = apply_stock_filters(features, execution_universe, config, rebalance_date)
    factors = calc_stock_factors(filtered, bars, industry_snapshot, config, rebalance_date)
    scored = rank_and_score(factors, config)
    snapshot = select_stocks(scored, config, rebalance_date)

    return snapshot.select(list(STOCK_SNAPSHOT_COLUMNS.keys())).sort(["industry", "vt_symbol"])
