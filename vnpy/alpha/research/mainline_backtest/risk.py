"""风控规则：什么时候该减仓。

这个文件回答一个问题：三条风控规则（大盘择时 / 无信号月清仓 / 个股止损）
各自在什么条件下触发。这里只有"判定"的纯函数——触发之后卖什么、怎么卖，
由 pipeline.py 主循环调用 execution.py 的卖出路径完成（风控只减仓，FR-007）。

三条规则的口径（对应 spec FR-002~005，细节见 research.md R1/R4/R5）：
    大盘择时   调仓执行日的前一交易日，沪深300 收盘 < 自身 60 日均线 → 弱市，
               该期清仓空仓；均线窗口不足时视为"通过"（不空仓）并记日志。
    无信号清仓 信号层在某个真实月末选不出任何主线行业 → 次一交易日清仓。
    个股止损   持仓收盘价较买入执行价（含滑点、后复权口径）回撤严格超过阈值
               → 次一交易日开盘卖出。
"""

from __future__ import annotations

import logging
import math
from datetime import date

import polars as pl

from .config import BacktestConfig

logger = logging.getLogger(__name__)


def compute_vol_target_scale(config: BacktestConfig, nav_rows: list[dict] | None) -> float:
    """S7 波动率目标：按最近 N 天组合日收益的年化标准差 σ 返回目标金额缩放系数。

    关闭时（portfolio_vol_target == 0）或数据不足 5 天日收益（= 6 个 nav 点）时
    返回 1.0，退化到基线等权行为（保证首个调仓期与逐字节回归）。
    否则 scale = clip(target / annual_vol, min, max)：
      - σ 低（市场平静）→ scale > 1，加仓；
      - σ 高（市场剧烈）→ scale < 1，降仓；
      - σ ≈ 0（合成数据 / 长时间横盘）→ 视为极度平静，取上限。
    复现性：日收益按 nav_rows 插入顺序累加计算方差，不做并行聚合。
    """
    if config.portfolio_vol_target <= 0 or nav_rows is None:
        return 1.0
    tail = nav_rows[-(config.portfolio_vol_window + 1):]
    if len(tail) < 6:
        return 1.0
    daily_returns: list[float] = []
    for i in range(1, len(tail)):
        prev = tail[i - 1]["nav"]
        curr = tail[i]["nav"]
        if prev > 0:
            daily_returns.append(curr / prev - 1.0)
    if len(daily_returns) < 5:
        return 1.0
    mean = sum(daily_returns) / len(daily_returns)
    sq = 0.0
    for r in daily_returns:
        diff = r - mean
        sq += diff * diff
    variance = sq / (len(daily_returns) - 1)
    daily_vol = math.sqrt(variance)
    annual_vol = daily_vol * math.sqrt(252)
    if annual_vol <= 0:
        return config.portfolio_vol_scale_max
    raw = config.portfolio_vol_target / annual_vol
    return max(config.portfolio_vol_scale_min, min(config.portfolio_vol_scale_max, raw))


def build_benchmark_ma(benchmark: pl.DataFrame, window: int) -> pl.DataFrame:
    """给基准日线加一列滚动均线，返回三列: datetime, benchmark_close, ma。

    窗口不足 window 天的行 ma 为 null（rolling_mean 默认行为），
    is_weak_market() 对 null 均线一律判"通过"。
    """
    return benchmark.sort("datetime").with_columns(
        pl.col("benchmark_close").rolling_mean(window).alias("ma")
    )


def is_weak_market(ma_table: pl.DataFrame, exec_day: date) -> dict:
    """择时判定：exec_day 是否处于弱市，返回判定详情字典。

    只看执行日**之前**最后一个基准交易日的收盘与均线（无未来信息，FR-002）。
    返回 {"weak": bool, "window_short": bool, "benchmark_close": x, "benchmark_ma": y}；
    window_short=True（历史不足一个均线窗口）时 weak 恒为 False。
    """
    prior = ma_table.filter(pl.col("datetime") < exec_day).tail(1)
    if prior.is_empty() or prior.get_column("ma")[0] is None:
        return {"weak": False, "window_short": True, "benchmark_close": None, "benchmark_ma": None}

    close = float(prior.get_column("benchmark_close")[0])
    ma = float(prior.get_column("ma")[0])
    return {"weak": close < ma, "window_short": False, "benchmark_close": close, "benchmark_ma": ma}


def check_timing(
    config: BacktestConfig,
    ma_table: pl.DataFrame | None,
    period: dict,
    day: date,
    quality_logs: list[dict],
) -> bool:
    """调仓日的择时判定：返回是否弱市，并把跳过/窗口不足记入质量日志。"""
    if not config.timing_enabled:
        return False

    verdict = is_weak_market(ma_table, day)
    if verdict["window_short"]:
        quality_logs.append(
            {"type": "timing_window_short", "datetime": str(day),
             "detail": f"基准历史不足 {config.timing_ma_window} 日均线窗口，择时视为通过"}
        )
        return False
    if verdict["weak"]:
        quality_logs.append(
            {"type": "timing_skip", "signal_date": str(period["signal_date"]),
             "benchmark_close": verdict["benchmark_close"], "benchmark_ma": verdict["benchmark_ma"],
             "detail": "基准收盘低于均线，跳过该期买入并清仓"}
        )
        logger.info("%s 择时弱市: 跳过 %s 信号的买入", day, period["signal_date"])
    return verdict["weak"]


def check_timing_daily(config: BacktestConfig, ma_table: pl.DataFrame | None, day: date) -> bool:
    """逐日择时判定（007）：任意交易日，前一日基准收盘 < 均线即弱市。

    与 check_timing 的判定口径完全一致（前一日收盘，无未来信息），
    区别只在检查频率：check_timing 只在调仓执行日调用，本函数每个交易日调用。
    历史不足一个均线窗口时视为"通过"（不清仓）。
    """
    if not (config.timing_enabled and config.timing_daily_enabled):
        return False
    verdict = is_weak_market(ma_table, day)
    return bool(verdict["weak"]) and not verdict["window_short"]


def find_no_signal_month_ends(
    selection: pl.DataFrame, trading_days: list[date], last_bar_day: date
) -> list[date]:
    """找出"信号层选不出主线"的真实月末，升序返回。

    真实月末 = 交易日历中每个自然月的最后一个交易日（排除日历末尾的
    不完整月，与 001 的月末定义一致）；再去掉有信号的月份，
    范围限定 [第一个信号日, last_bar_day]。
    """
    calendar = pl.DataFrame({"trade_date": trading_days}).with_columns(
        pl.col("trade_date").dt.strftime("%Y-%m").alias("month")
    )
    last_calendar_month = calendar.get_column("month").max()
    month_ends = (
        calendar.group_by("month")
        .agg(pl.col("trade_date").max().alias("month_end"))
        .filter(pl.col("month") != last_calendar_month)  # 日历末月可能不完整
        .sort("month_end")
    )

    signal_months = set(
        selection.get_column("rebalance_date")
        .dt.strftime("%Y-%m")
        .unique()
        .to_list()
    )
    first_signal = selection.get_column("rebalance_date").min()

    return (
        month_ends.filter(
            ~pl.col("month").is_in(sorted(signal_months))
            & (pl.col("month_end") >= first_signal)
            & (pl.col("month_end") <= last_bar_day)
        )
        .get_column("month_end")
        .to_list()
    )


def scan_stop_loss(positions: dict, pending_sells: dict, config: BacktestConfig) -> list[dict]:
    """止损扫描：返回当日收盘触发止损的持仓明细列表（供记日志与挂单）。

    触发条件：当日收盘（后复权，停牌日为冻结价）较买入执行价的回撤
    **严格大于** stop_loss_rate。买入执行价的后复权口径 = 买入日后复权开盘
    × (1 + 滑点率)，与账户实际付出的成本一致。
    已挂在 pending_sells 里的持仓跳过（避免重复触发）。
    """
    triggered: list[dict] = []
    for vt_symbol in sorted(positions.keys()):
        if vt_symbol in pending_sells:
            continue
        position = positions[vt_symbol]
        entry_price_adj = position["buy_open_adj"] * (1 + config.slippage_rate)
        drawdown = position["last_close_adj"] / entry_price_adj - 1
        if drawdown < -config.stop_loss_rate - 1e-9:   # 容差防浮点噪声误判"恰好等于阈值"
            triggered.append({"vt_symbol": vt_symbol, "drawdown": drawdown})
    return triggered
