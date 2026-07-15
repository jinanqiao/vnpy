"""流水线编排：逐个交易日推进回测，并把结果落盘。

这个文件回答一个问题：一次完整的回测是怎么跑起来的。

run_mainline_backtest() 的主循环逐个交易日做八件事：
    1. 退市检查：行情永久消失的持仓按最后价格强制清仓；
    2. 补卖：之前卖不出去（停牌/跌停/止损顺延）的持仓今天再试一次；
    3. 无信号清仓：开关打开且信号层上月选不出主线 → 卖光旧仓（风控）；
    4. 逐日择时清仓：开关打开且前一日基准跌破均线 → 不等调仓日直接清仓（风控 007）；
    5. 逐日再入场：开关打开且因择时空仓、前一日基准收复均线 → 不等调仓日
       直接买回当期清单（风控 008，与第 4 步对称）；
    6. 调仓：今天是某期信号的执行日 → 先做择时判定（风控），弱市清仓
       空仓，正常则卖旧买新（等权）；
    7. 估值：收盘后给组合拍快照（净值 + 逐笔持仓）；
    8. 止损扫描：开关打开时按收盘价检查每笔持仓，触发者次日卖出（风控）。

产物六件套（对照 contracts/artifacts-schema.md）：
    config.json / nav.parquet / positions.parquet / trades.parquet /
    metrics.json / report.md
"""

from __future__ import annotations

import logging
from datetime import date

import polars as pl

from .config import BacktestConfig
from .data_loader import (
    build_periods,
    load_bars,
    load_benchmark,
    load_execution_universe,
    load_selection,
    load_trading_days,
    next_trading_day,
)
from .execution import (
    TRADES_SCHEMA,
    build_price_lookup,
    buy_stock,
    check_buy_blocked,
    check_sell_blocked,
    find_last_bar_dates,
    sell_all_positions,
    sell_position,
    sell_symbols,
    trade_row,
)
from .metrics import build_metrics_summary
from .portfolio import POSITIONS_SCHEMA, attach_benchmark, snapshot_day, trade_costs
from .report import write_artifacts
from .risk import (
    build_benchmark_ma,
    check_timing,
    check_timing_daily,
    find_no_signal_month_ends,
    scan_stop_loss,
)

logger = logging.getLogger(__name__)


def run_mainline_backtest(config: BacktestConfig) -> dict:
    """执行完整回测，返回 nav/positions/trades/metrics 与产物目录路径。"""
    selection = load_selection(config)
    if selection.is_empty():
        raise ValueError("选股清单为空（检查 selection 文件与 start/end 范围）")

    adjusted_bars = load_bars(config, adjusted=True)
    unadjusted_bars = load_bars(config, adjusted=False)
    benchmark = load_benchmark(config)
    universe = load_execution_universe(config)
    trading_days = load_trading_days(config)

    symbols = selection.get_column("vt_symbol").unique().sort().to_list()
    lookup = build_price_lookup(adjusted_bars, unadjusted_bars, symbols)
    last_bar_dates = find_last_bar_dates(adjusted_bars, symbols)
    tradable_set = {
        (row["datetime"], row["vt_symbol"])
        for row in universe.filter(
            pl.col("vt_symbol").is_in(symbols) & pl.col("in_execution")
        ).iter_rows(named=True)
    }

    periods, quality_logs = build_periods(selection, trading_days, adjusted_bars)
    logger.info("回测就绪: %d 个调仓期, %d 只候选股票", len(periods), len(symbols))

    # ---- 风控准备（开关关闭时不构建，保持 002 基线路径零改动） ----
    last_bar_day = adjusted_bars.get_column("datetime").max()
    ma_table = build_benchmark_ma(benchmark, config.timing_ma_window) if config.timing_enabled else None
    no_signal_exits: dict[date, date] = {}
    if config.no_signal_exit_enabled:
        for month_end in find_no_signal_month_ends(selection, trading_days, last_bar_day):
            exit_day = next_trading_day(trading_days, month_end)
            if exit_day is not None and exit_day <= last_bar_day:
                no_signal_exits[exit_day] = month_end

    result = _run_daily_loop(
        config, periods, lookup, last_bar_dates, tradable_set, trading_days,
        last_bar_day, ma_table, no_signal_exits, quality_logs,
    )

    nav_frame = attach_benchmark(result["nav_rows"], benchmark)
    # 008 再入场后同一 (signal_date, vt_symbol, side) 可能出现多笔，补 planned_date 定序
    trades = pl.DataFrame(result["trades"], schema=TRADES_SCHEMA).sort(
        ["signal_date", "vt_symbol", "side", "planned_date"]
    )
    positions = pl.DataFrame(result["position_rows"], schema=POSITIONS_SCHEMA).sort(
        ["datetime", "vt_symbol"]
    )

    rebalance_dates = [period["signal_date"] for period in periods]
    metrics = build_metrics_summary(nav_frame, trades, rebalance_dates, config.initial_capital)

    risk_summary = {
        "timing_enabled": config.timing_enabled,
        "timing_skips": sum(1 for log in quality_logs if log["type"] == "timing_skip"),
        "timing_daily_enabled": config.timing_daily_enabled,
        "timing_daily_exits": sum(1 for log in quality_logs if log["type"] == "timing_daily_exit"),
        "timing_reentry_enabled": config.timing_reentry_enabled,
        "timing_reentries": sum(1 for log in quality_logs if log["type"] == "timing_reentry"),
        "no_signal_exit_enabled": config.no_signal_exit_enabled,
        "no_signal_exits": sum(1 for log in quality_logs if log["type"] == "no_signal_exit"),
        "stop_loss_enabled": config.stop_loss_enabled,
        "stop_loss_trades": trades.filter(pl.col("reason") == "stop_loss").height,
    }
    output_dir = write_artifacts(
        config, nav_frame, positions, trades, metrics, quality_logs, risk_summary
    )
    logger.info("完成: 产物已写入 %s", output_dir)

    return {
        "nav": nav_frame, "positions": positions, "trades": trades,
        "metrics": metrics, "output_dir": output_dir,
    }


def _run_daily_loop(
    config: BacktestConfig,
    periods: list[dict],
    lookup: dict,
    last_bar_dates: dict,
    tradable_set: set,
    trading_days: list[date],
    last_bar_day: date,
    ma_table: pl.DataFrame | None,
    no_signal_exits: dict,
    quality_logs: list[dict],
) -> dict:
    """逐交易日推进的主循环（回测的心脏，从上到下读一遍就是完整流程）。"""
    positions: dict = {}
    pending_sells: dict = {}       # vt_symbol -> (原计划卖出日, reason)（等待补卖）
    cash = config.initial_capital
    costs_paid = 0.0
    trades: list[dict] = []
    nav_rows: list[dict] = []
    position_rows: list[dict] = []
    current_period: dict | None = None   # 最近一个已到执行日的调仓期（= 当前目标清单）
    reentry_target: dict | None = None   # 因择时空仓、等待市场转强后买回的调仓期（008）

    exec_map = {period["exec_day"]: period for period in periods}
    valuation_days = [
        day for day in trading_days
        if periods and periods[0]["exec_day"] <= day <= last_bar_day
    ]

    for day in valuation_days:
        # --- 1. 退市检查：行情已永久消失的持仓按最后价格强平 ---
        for vt_symbol in sorted(list(positions.keys())):
            if day > last_bar_dates.get(vt_symbol, day):
                trade, cash_in = sell_position(lookup, positions[vt_symbol], day, day, config, forced=True)
                trades.append(trade)
                cash += cash_in
                costs_paid += trade_costs(trade)
                positions.pop(vt_symbol)
                pending_sells.pop(vt_symbol, None)
                quality_logs.append(
                    {"type": "forced_close", "datetime": str(day), "vt_symbol": vt_symbol,
                     "detail": "行情永久消失（疑似退市），按最后可得价格强制清仓"}
                )

        # --- 2. 补卖：之前卖不出去的持仓今天再试（含止损顺延单） ---
        for vt_symbol in sorted(list(pending_sells.keys())):
            planned, reason = pending_sells[vt_symbol]
            if planned <= day and check_sell_blocked(lookup, vt_symbol, day) is None:
                pending_sells.pop(vt_symbol)
                trade, cash_in = sell_position(
                    lookup, positions.pop(vt_symbol), day, planned, config, reason=reason
                )
                trades.append(trade)
                cash += cash_in
                costs_paid += trade_costs(trade)

        # --- 3. 无信号清仓（风控）：信号层上月选不出主线 → 卖光旧仓 ---
        if day in no_signal_exits and positions:
            sell_trades, cash, costs_paid = sell_all_positions(
                lookup, positions, pending_sells, day, config, cash, costs_paid,
                reason="no_signal_exit",
            )
            trades.extend(sell_trades)
            reentry_target = None   # 新的月份没有目标清单，不能把上月旧清单买回来
            quality_logs.append(
                {"type": "no_signal_exit", "month_end": str(no_signal_exits[day]),
                 "exit_date": str(day), "n_positions": len(sell_trades) + len(pending_sells),
                 "detail": "信号层无主线行业，清仓持币"}
            )

        # --- 4. 逐日择时清仓（风控 007）：弱市不等调仓日，信号次日开盘直接离场 ---
        # 调仓执行日跳过（第 5 步的 check_timing 会处理，避免重复判定/记录）。
        if (
            positions and day not in exec_map
            and check_timing_daily(config, ma_table, day)
        ):
            n_before = len(positions)
            sell_trades, cash, costs_paid = sell_all_positions(
                lookup, positions, pending_sells, day, config, cash, costs_paid,
                reason="timing_exit",
            )
            trades.extend(sell_trades)
            if config.timing_reentry_enabled:
                reentry_target = current_period   # 市场转强后把当期清单买回来
            quality_logs.append(
                {"type": "timing_daily_exit", "datetime": str(day), "n_positions": n_before,
                 "detail": "前一日基准收盘低于均线，逐日择时清仓"}
            )
            logger.info("%s 逐日择时弱市: 清仓 %d 只", day, n_before)

        # --- 5. 逐日再入场（风控 008）：因择时空仓、前一日基准收复均线 → 买回当期清单 ---
        # 与第 4 步对称：清仓逐日触发，再入场也逐日触发，不用空仓等到下个调仓日。
        # 判定口径与择时完全一致（前一日收盘 vs 均线，无未来信息）；
        # 弱市与强市互斥，同一天不可能既清仓又再入场。
        if (
            config.timing_reentry_enabled
            and reentry_target is not None
            and not positions and not pending_sells
            and day not in exec_map
            and not check_timing_daily(config, ma_table, day)
        ):
            equity = nav_rows[-1]["nav"] * config.initial_capital if nav_rows else config.initial_capital
            cash, costs_paid = _buy_selection(
                config, reentry_target, lookup, tradable_set, positions, trades,
                cash, costs_paid, equity, day,
                nav_rows=nav_rows,
            )
            quality_logs.append(
                {"type": "timing_reentry", "datetime": str(day),
                 "signal_date": str(reentry_target["signal_date"]),
                 "detail": "前一日基准收盘回到均线上方，买回当期清单"}
            )
            logger.info("%s 逐日再入场: 买回 %s 期清单", day, reentry_target["signal_date"])
            reentry_target = None

        # --- 6. 调仓：今天是某期信号的执行日（先择时，后卖旧买新） ---
        period = exec_map.get(day)
        if period is not None:
            current_period = period
            weak = check_timing(config, ma_table, period, day, quality_logs)
            equity = nav_rows[-1]["nav"] * config.initial_capital if nav_rows else config.initial_capital

            # 6a. 先卖。弱市：清掉全部旧持仓（timing_exit）；正常 + 差量模式：
            # 只卖出局的（连任的原仓保留，省一轮双边成本）；正常 + 基线模式：全卖。
            if not weak and config.delta_rebalance_enabled:
                target_symbols = {vt_symbol for vt_symbol, _ in period["stocks"]}
                to_sell = sorted(s for s in positions.keys() if s not in target_symbols)
                sell_trades, cash, costs_paid = sell_symbols(
                    lookup, positions, pending_sells, to_sell, day, config, cash, costs_paid,
                )
            else:
                sell_trades, cash, costs_paid = sell_all_positions(
                    lookup, positions, pending_sells, day, config, cash, costs_paid,
                    reason="timing_exit" if weak else None,
                )
            trades.extend(sell_trades)

            # 6b. 后买：弱市跳过全部买入（空仓持币）；正常则等权买入新清单
            # （差量模式下连任的持仓静默跳过，不产生交易记录）
            if not weak:
                cash, costs_paid = _buy_selection(
                    config, period, lookup, tradable_set, positions, trades,
                    cash, costs_paid, equity, day,
                    skip_held=config.delta_rebalance_enabled,
                    nav_rows=nav_rows,
                )
                reentry_target = None   # 新一期已正常建仓，旧的再入场目标作废
            elif config.timing_reentry_enabled:
                reentry_target = period   # 弱市跳过建仓：转强后把本期清单买回来

        # --- 7. 收盘估值 ---
        nav_row, day_positions = snapshot_day(lookup, positions, cash, costs_paid, day, config)
        nav_rows.append(nav_row)
        position_rows.extend(day_positions)

        for row in day_positions:  # 长期停牌记录（超过阈值只记一次）
            symbol = row["vt_symbol"]
            if positions[symbol]["frozen_days"] == config.suspend_freeze_days:
                quality_logs.append(
                    {"type": "long_suspension", "datetime": str(day), "vt_symbol": symbol,
                     "detail": f"停牌冻结估值已达 {config.suspend_freeze_days} 个交易日"}
                )

        # --- 8. 止损扫描（风控）：收盘触发，次一交易日卖出 ---
        if config.stop_loss_enabled and positions:
            for hit in scan_stop_loss(positions, pending_sells, config):
                next_day = next_trading_day(trading_days, day)
                if next_day is None:
                    continue   # 日历走到头，无法执行
                pending_sells[hit["vt_symbol"]] = (next_day, "stop_loss")
                quality_logs.append(
                    {"type": "stop_loss_trigger", "datetime": str(day),
                     "vt_symbol": hit["vt_symbol"], "drawdown": round(hit["drawdown"], 6),
                     "detail": f"收盘回撤 {hit['drawdown']:.1%} 超过阈值 {config.stop_loss_rate:.0%}"}
                )

    return {"trades": trades, "nav_rows": nav_rows, "position_rows": position_rows}


def _buy_selection(
    config: BacktestConfig,
    period: dict,
    lookup: dict,
    tradable_set: set,
    positions: dict,
    trades: list[dict],
    cash: float,
    costs_paid: float,
    equity: float,
    day: date,
    skip_held: bool = False,
    nav_rows: list[dict] | None = None,
) -> tuple[float, float]:
    """等权买入当期清单（目标金额 = 期初权益 / 入选数），返回更新后的 (现金, 累计成本)。

    skip_held=True（差量调仓）：已持有的连任股票静默跳过，不产生交易记录。
    max_position_weight>0（S15）：单股目标金额上限为 max_position_weight * equity，
    多余现金保留（不再分摊到其他股票，避免规模小时单股敞口膨胀）。
    nav_rows + portfolio_vol_target>0（S7）：按最近 N 天组合日收益 σ 缩放目标金额。
    """
    from .risk import compute_vol_target_scale
    vol_scale = compute_vol_target_scale(config, nav_rows)
    target_amount = (equity * vol_scale) / len(period["stocks"])
    if config.max_position_weight > 0:
        cap = config.max_position_weight * equity
        if target_amount > cap:
            target_amount = cap
    for vt_symbol, industry in sorted(period["stocks"]):
        if vt_symbol in positions and skip_held:  # 差量调仓：连任持仓原样保留
            continue
        if vt_symbol in positions:  # 还挂着没卖掉的同名持仓，无法重复买入
            blocked = "suspended"
        else:
            blocked = check_buy_blocked(lookup, tradable_set, vt_symbol, day)

        if blocked is not None:
            trades.append(
                trade_row(period["signal_date"], day, vt_symbol, industry, "buy", "abandoned", blocked)
            )
            continue

        trade, position, cash_out = buy_stock(
            lookup, vt_symbol, industry, target_amount, cash, day,
            period["signal_date"], config,
        )
        trades.append(trade)
        if position is not None:
            positions[vt_symbol] = position
            cash -= cash_out
            costs_paid += trade_costs(trade)

    filled = sum(
        1 for t in trades
        if t["signal_date"] == period["signal_date"] and t["status"] == "filled" and t["side"] == "buy"
    )
    logger.info("%s 执行 %s 信号: 买入 %d/%d 只, 现金 %.0f",
                day, period["signal_date"], filled, len(period["stocks"]), cash)
    return cash, costs_paid
