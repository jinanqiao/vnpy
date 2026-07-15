"""行业层：算出每个行业的动量和上涨广度，选出当期主线行业。

这个文件回答一个问题：这个月市场的"主线"在哪几个行业。

判断分三步（对应 spec 的 FR-001 ~ FR-003）：
    1. 行业动量：行业内成分股等权日收益复合成行业净值，看主窗口（默认 20 日）
       和确认窗口（默认 5 日）涨幅——动量定强弱；
    2. 上涨广度：行业内广度窗口（默认 20 日）内上涨的股票占比——广度验真伪，
       防止一两只权重股独涨把行业指数拉高的"伪主线"；
    3. 入选条件：两个动量窗口排名都进门槛（默认前 5），且广度 >= 60%，
       满足者中按主窗口动量取前 N 个（默认 3 个）。

历史提示：列名 mom_60 / mom_20 / breadth_60 沿用最初默认窗口（60/20/60）的命名，
实际窗口长度以 MainlineConfig 为准（当前默认 20/5/20），含义分别是
"主窗口动量 / 确认窗口动量 / 广度窗口上涨占比"。

对外只需要调用 calc_industry_snapshot()，返回一张"行业信号快照"表。
"""

from __future__ import annotations

from datetime import date

import polars as pl

from .config import MainlineConfig

# 行业信号快照的固定列结构（对照 specs 的 data-model.md）
SNAPSHOT_COLUMNS = {
    "rebalance_date": pl.Date,
    "industry": pl.Utf8,
    "mom_60": pl.Float64,
    "mom_20": pl.Float64,
    "rank_60": pl.Int32,
    "rank_20": pl.Int32,
    "breadth_60": pl.Float64,
    "member_count": pl.Int32,
    "selected": pl.Boolean,
    "industry_streak": pl.Int32,   # 主线连续入选月数（004 新增，非主线行业为 null）
    "reject_reason": pl.Utf8,
}


def window_trade_dates(bars: pl.DataFrame, rebalance_date: date, window: int) -> list[date]:
    """取调仓日（含）之前最近的 window+1 个交易日。

    要算 60 日涨幅需要 61 个价格点（首尾各一个），所以是 window+1。
    历史不够长时返回空列表，由调用方按"窗口不足"处理。
    """
    dates = (
        bars.filter(pl.col("datetime") <= rebalance_date)
        .get_column("datetime")
        .unique()
        .sort()
        .to_list()
    )
    if len(dates) < window + 1:
        return []
    return dates[-(window + 1):]


def stock_returns_in_window(bars: pl.DataFrame, dates: list[date]) -> pl.DataFrame:
    """计算窗口内每只股票的逐日收益和整段累计收益。

    返回两张信息合一的表，列:
        vt_symbol, datetime, daily_return   （逐日，窗口首日无收益故不含首日）
        cum_return                          （整段累计，仅在每只股票的最后一行给出）

    只统计"窗口首日和末日都有收盘价"的股票——中途上市或长期停牌的股票
    没法算完整窗口的收益，直接不参与（宁缺毋滥，不用默认值补）。
    """
    empty_schema = {"vt_symbol": pl.Utf8, "datetime": pl.Date, "daily_return": pl.Float64, "running_return": pl.Float64}
    if not dates:
        return pl.DataFrame(schema=empty_schema)

    window = bars.filter(pl.col("datetime").is_in(dates)).sort(["vt_symbol", "datetime"])

    first_day, last_day = dates[0], dates[-1]
    has_both_ends = (
        window.group_by("vt_symbol")
        .agg(
            (pl.col("datetime").min() == first_day).alias("has_first"),
            (pl.col("datetime").max() == last_day).alias("has_last"),
        )
        .filter(pl.col("has_first") & pl.col("has_last"))
        .get_column("vt_symbol")
        .to_list()
    )
    window = window.filter(pl.col("vt_symbol").is_in(has_both_ends))

    window = window.with_columns(
        (pl.col("close") / pl.col("close").shift(1).over("vt_symbol") - 1).alias("daily_return"),
        (pl.col("close") / pl.col("close").first().over("vt_symbol") - 1).alias("running_return"),
    )
    return window


def industry_momentum(returns: pl.DataFrame, industry_map: pl.DataFrame) -> pl.DataFrame:
    """行业动量：成分股等权日收益复合成行业净值的整段涨幅（FR-001）。

    每天把行业内所有股票的日收益取平均（等权），再把这些日均收益
    按天复利连乘，得到"行业净值涨了多少"。返回列: industry, momentum。
    """
    with_industry = (
        returns.drop_nulls("daily_return")
        .join(industry_map, on="vt_symbol", how="inner")
        # join 的输出行序不稳定，先排死顺序，保证浮点求和顺序固定（逐字节可复现）
        .sort(["industry", "datetime", "vt_symbol"])
    )
    daily_mean = (
        with_industry.group_by(["industry", "datetime"], maintain_order=True)
        .agg(pl.col("daily_return").mean().alias("industry_return"))
        .sort(["industry", "datetime"])
    )
    nav = daily_mean.group_by("industry").agg(
        ((pl.col("industry_return") + 1.0).product() - 1.0).alias("momentum")
    )
    return nav


def industry_breadth(returns: pl.DataFrame, industry_map: pl.DataFrame) -> pl.DataFrame:
    """上涨广度：行业内整段累计收益为正的股票占比（FR-002）。

    广度高 = 行业普涨（资金全面进驻的真主线）；
    广度低 = 少数权重股独涨（要回避的伪主线）。
    返回列: industry, breadth, member_count。
    """
    last_rows = (
        returns.sort(["vt_symbol", "datetime"])
        .group_by("vt_symbol")
        .agg(pl.col("running_return").last().alias("cum_return"))
        .join(industry_map, on="vt_symbol", how="inner")
    )
    stats = last_rows.group_by("industry").agg(
        (pl.col("cum_return") > 0).mean().alias("breadth"),
        pl.len().cast(pl.Int32).alias("member_count"),
    )
    return stats


def calc_industry_snapshot(
    bars: pl.DataFrame,
    industry_map: pl.DataFrame,
    config: MainlineConfig,
    rebalance_date: date,
) -> pl.DataFrame:
    """生成一个调仓日的完整行业信号快照，并标记主线行业（FR-003）。

    入选规则：
        1. 成分股足够多（member_count >= min_industry_members），否则原因 member_count；
        2. 主窗口和确认窗口动量排名都 <= industry_rank_gate，否则原因 rank_gate；
        3. 上涨广度 >= breadth_min，否则原因 breadth；
        4. 满足者按主窗口动量从高到低取前 industry_top_n 个，落选者原因 rank_gate。
    历史不足以计算窗口时返回空表（0 行），由流水线记录原因。
    """
    dates_main = window_trade_dates(bars, rebalance_date, config.mom_window)
    dates_confirm = window_trade_dates(bars, rebalance_date, config.confirm_window)
    dates_breadth = window_trade_dates(bars, rebalance_date, config.breadth_window)
    if not dates_main or not dates_confirm or not dates_breadth:
        return pl.DataFrame(schema=SNAPSHOT_COLUMNS)

    returns_main = stock_returns_in_window(bars, dates_main)
    returns_confirm = stock_returns_in_window(bars, dates_confirm)
    # 广度窗口与主窗口相同时直接复用，避免重复计算
    returns_breadth = (
        returns_main
        if config.breadth_window == config.mom_window
        else stock_returns_in_window(bars, dates_breadth)
    )

    snapshot = (
        industry_momentum(returns_main, industry_map).rename({"momentum": "mom_60"})
        .join(industry_momentum(returns_confirm, industry_map).rename({"momentum": "mom_20"}), on="industry", how="full", coalesce=True)
        .join(industry_breadth(returns_breadth, industry_map).rename({"breadth": "breadth_60"}), on="industry", how="left")
    )

    # 排名只在"成分股足够多"的行业之间进行（1 = 动量最强）
    snapshot = snapshot.with_columns(
        (pl.col("member_count").fill_null(0) >= config.min_industry_members).alias("eligible")
    )
    ranked = snapshot.filter(pl.col("eligible")).with_columns(
        pl.col("mom_60").rank(method="ordinal", descending=True).cast(pl.Int32).alias("rank_60"),
        pl.col("mom_20").rank(method="ordinal", descending=True).cast(pl.Int32).alias("rank_20"),
    )
    unranked = snapshot.filter(~pl.col("eligible")).with_columns(
        pl.lit(None, dtype=pl.Int32).alias("rank_60"),
        pl.lit(None, dtype=pl.Int32).alias("rank_20"),
    )
    snapshot = pl.concat([ranked, unranked])

    # 三道门槛逐一检查，记录第一个不满足的原因
    gate = config.industry_rank_gate
    passes_rank = (pl.col("rank_60") <= gate) & (pl.col("rank_20") <= gate)
    passes_breadth = pl.col("breadth_60") >= config.breadth_min

    snapshot = snapshot.with_columns(
        pl.when(~pl.col("eligible"))
        .then(pl.lit("member_count"))
        .when(~passes_rank.fill_null(False))
        .then(pl.lit("rank_gate"))
        .when(~passes_breadth.fill_null(False))
        .then(pl.lit("breadth"))
        .otherwise(None)
        .alias("reject_reason")
    )

    # 候选行业中按 60 日动量取前 N；落选的候选行业原因也记为 rank_gate
    candidates = (
        snapshot.filter(pl.col("reject_reason").is_null())
        .sort("mom_60", descending=True)
        .get_column("industry")
        .to_list()
    )
    winners = candidates[: config.industry_top_n]

    snapshot = snapshot.with_columns(
        pl.col("industry").is_in(winners).alias("selected"),
        pl.when(pl.col("reject_reason").is_null() & ~pl.col("industry").is_in(winners))
        .then(pl.lit("rank_gate"))
        .otherwise(pl.col("reject_reason"))
        .alias("reject_reason"),
    )

    snapshot = snapshot.with_columns(
        pl.lit(rebalance_date).alias("rebalance_date"),
        pl.lit(None, dtype=pl.Int32).alias("industry_streak"),  # 月龄由 apply_freshness 填写
    )
    return snapshot.select(list(SNAPSHOT_COLUMNS.keys())).sort("industry")


def apply_freshness(
    snapshot: pl.DataFrame,
    prev_streak: dict[str, int],
    config: MainlineConfig,
) -> tuple[pl.DataFrame, dict[str, int], list[tuple[str, int]]]:
    """给行业快照标注主线月龄，并按新鲜度上限过滤老主线（004 FR-001~FR-004）。

    月龄口径：行业只要按"原始主线"（三道门槛 + TopN）达标就计数——
    上一个快照期也是原始主线则月龄 +1，否则重置为 1。月龄不受过滤本身影响，
    避免行业"隔月复活"（第 2 月被过滤后第 3 月又被误当成新主线买回）。

    过滤规则：max_industry_streak > 0 且月龄超过上限的行业整期不建仓，
    在快照里记 selected=False、reject_reason="stale"（月龄保留，方便审计）。

    参数 prev_streak 是上一个快照期的 {行业: 月龄}；返回三元组：
        (标注并过滤后的快照, 本期新的 prev_streak, 被过滤的 [(行业, 月龄)] 列表)
    """
    raw_mainline = snapshot.filter(pl.col("selected")).get_column("industry").to_list()
    streaks = {industry: prev_streak.get(industry, 0) + 1 for industry in raw_mainline}

    streak_frame = pl.DataFrame(
        {"industry": list(streaks.keys()), "streak_value": list(streaks.values())},
        schema={"industry": pl.Utf8, "streak_value": pl.Int32},
    )
    snapshot = (
        snapshot.join(streak_frame, on="industry", how="left")
        .with_columns(pl.col("streak_value").alias("industry_streak"))
        .drop("streak_value")
    )

    stale: list[tuple[str, int]] = []
    if config.max_industry_streak > 0:
        stale = sorted(
            (industry, streak)
            for industry, streak in streaks.items()
            if streak > config.max_industry_streak
        )
    stale_names = [industry for industry, _ in stale]

    snapshot = snapshot.with_columns(
        pl.when(pl.col("industry").is_in(stale_names))
        .then(pl.lit(False))
        .otherwise(pl.col("selected"))
        .alias("selected"),
        pl.when(pl.col("industry").is_in(stale_names))
        .then(pl.lit("stale"))
        .otherwise(pl.col("reject_reason"))
        .alias("reject_reason"),
    )
    return snapshot.select(list(SNAPSHOT_COLUMNS.keys())).sort("industry"), streaks, stale
