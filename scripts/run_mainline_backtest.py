"""命令行入口：对 001 的选股信号执行历史回测。

用法示例（在仓库根目录执行）：

    # 全历史回测
    python scripts/run_mainline_backtest.py \
        --selection outputs/mainline/<run_id>/selection.parquet --name full_history

    # 零成本对照
    python scripts/run_mainline_backtest.py --selection <path> --zero-cost --name no_cost

    # 覆盖任意参数
    echo '{"slippage_rate": 0.003}' > /tmp/bt.json
    python scripts/run_mainline_backtest.py --selection <path> --config-json /tmp/bt.json

参数与行为契约见 specs/002-mainline-backtest/contracts/cli-contract.md。
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace

from vnpy.alpha.research.mainline_backtest import (
    BacktestConfig,
    load_config_from_json,
    run_mainline_backtest,
    zero_cost,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="对主线强势股信号执行历史回测")
    parser.add_argument("--selection", required=True, help="001 产物 selection.parquet 路径")
    parser.add_argument("--start", default="", help="回测起始调仓期（含），如 2023-01-01")
    parser.add_argument("--end", default="", help="回测结束调仓期（含）")
    parser.add_argument("--data-dir", default="data", help="数据湖根目录")
    parser.add_argument("--output-dir", default="outputs/mainline_backtest", help="产物根目录")
    parser.add_argument("--name", default="mainline_backtest", help="实验名，拼进产物目录名")
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0, help="初始资金（元）")
    parser.add_argument("--zero-cost", action="store_true", help="佣金/印花税/滑点全部置 0（对照实验）")
    parser.add_argument("--config-json", default="", help="JSON 文件路径，覆盖任意配置字段")
    parser.add_argument("--timing", action="store_true", help="打开大盘择时开关（基准跌破均线则清仓空仓）")
    parser.add_argument("--timing-ma-window", type=int, default=60, help="择时均线窗口（交易日数）")
    parser.add_argument(
        "--timing-daily", action="store_true",
        help="逐日择时：弱市不等调仓日直接清仓（需要同时打开 --timing）",
    )
    parser.add_argument(
        "--timing-reentry", action="store_true",
        help="逐日再入场：择时空仓后基准收复均线次日买回当期清单（需要 --timing-daily）",
    )
    parser.add_argument("--no-signal-exit", action="store_true", help="打开无信号月清仓开关")
    parser.add_argument(
        "--delta-rebalance", action="store_true",
        help="差量调仓：调仓日只交易名单差集，连任持仓保留不动（省重叠部分的双边成本）",
    )
    parser.add_argument("--stop-loss", action="store_true", help="打开个股止损开关")
    parser.add_argument("--stop-loss-rate", type=float, default=0.20, help="止损阈值（回撤比例，0<X<1）")
    parser.add_argument(
        "--max-position-weight", type=float, default=0.0,
        help="S15 单股权重上限（0=关闭；0<x<=1 时目标金额封顶为 x*equity，多余现金保留）",
    )
    parser.add_argument(
        "--portfolio-vol-target", type=float, default=0.0,
        help="S7 组合波动率目标（0=关闭；如 0.10 = 10% 年化）",
    )
    parser.add_argument(
        "--portfolio-vol-window", type=int, default=20,
        help="S7 波动率采样窗口（默认 20 日）",
    )
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> BacktestConfig:
    """把命令行参数翻译成 BacktestConfig（--config-json 先生效，命令行最后覆盖）。"""
    config = BacktestConfig()
    if args.config_json:
        config = load_config_from_json(args.config_json, base=config)

    config = replace(
        config,
        selection_path=args.selection,
        start=args.start,
        end=args.end,
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        name=args.name,
        initial_capital=args.initial_capital,
        timing_enabled=args.timing or config.timing_enabled,
        timing_ma_window=args.timing_ma_window,
        timing_daily_enabled=args.timing_daily or config.timing_daily_enabled,
        timing_reentry_enabled=args.timing_reentry or config.timing_reentry_enabled,
        no_signal_exit_enabled=args.no_signal_exit or config.no_signal_exit_enabled,
        delta_rebalance_enabled=args.delta_rebalance or config.delta_rebalance_enabled,
        stop_loss_enabled=args.stop_loss or config.stop_loss_enabled,
        stop_loss_rate=args.stop_loss_rate,
        max_position_weight=(
            args.max_position_weight if args.max_position_weight > 0
            else config.max_position_weight
        ),
        portfolio_vol_target=(
            args.portfolio_vol_target if args.portfolio_vol_target > 0
            else config.portfolio_vol_target
        ),
        portfolio_vol_window=args.portfolio_vol_window,
    )
    if args.zero_cost:
        config = zero_cost(config)
    return config


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    try:
        run_mainline_backtest(build_config(args))
    except (FileNotFoundError, ValueError) as error:
        print(f"回测失败: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
