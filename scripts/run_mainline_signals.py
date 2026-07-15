"""命令行入口：运行主线强势股月度选股信号。

用法示例（在仓库根目录执行）：

    # 全历史运行
    python scripts/run_mainline_signals.py --name all_history

    # 只跑一段区间
    python scripts/run_mainline_signals.py --start 2025-01-01 --end 2025-06-30 --name h1_2025

    # 龙头池（沪深300 + 中证500）+ 三因子等权对照
    python scripts/run_mainline_signals.py --universe leaders --equal-weights --name leaders_eq

参数与行为契约见 specs/001-mainline-trend-strategy/contracts/cli-contract.md。
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace

from vnpy.alpha.research.mainline import MainlineConfig, load_config_from_json, run_mainline_signals


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成主线强势股月度选股信号")
    parser.add_argument("--start", default="", help="起始日期（含），如 2022-03-01；缺省为数据完整区间")
    parser.add_argument("--end", default="", help="结束日期（含），如 2026-06-30")
    parser.add_argument("--data-dir", default="data", help="数据湖根目录")
    parser.add_argument("--output-dir", default="outputs/mainline", help="产物根目录")
    parser.add_argument("--name", default="mainline_signals", help="实验名，会拼进产物目录名")
    parser.add_argument("--universe", default="all_a", choices=["all_a", "leaders"], help="股票池")
    parser.add_argument(
        "--industry-source", default="gics1", choices=["gics1", "sw1"],
        help="行业口径：gics1 = GICS 一级（11 个）；sw1 = 申万一级（31 个，更细）",
    )
    parser.add_argument(
        "--industry-mode", default="none", choices=["none", "select", "regime"],
        help="行业层角色：none = 不要行业层，逐期全市场选股（默认）；"
             "select = 选行业后行业内选股；regime = 只当市场状态开关，全市场选股",
    )
    parser.add_argument(
        "--trend-filters", default=None, choices=["on", "off"],
        help="趋势三关硬过滤（均线多头/乖离/距新高）：off = 只留可交易+上市满一年（默认）；on = 恢复五项过滤",
    )
    parser.add_argument(
        "--score-weights", default="",
        help='打分权重 "rs,nh,vol"，允许负数表示因子反向，如 "-0.40,0.35,-0.25"（默认）',
    )
    parser.add_argument(
        "--volatility-weight", type=float, default=None,
        help="低波动率因子权重（负数 = 波动越低分越高；0 = 关闭，默认）",
    )
    parser.add_argument(
        "--illiq-weight", type=float, default=None,
        help="Amihud 非流动性因子权重（正数 = 越不流动分越高；0 = 关闭，默认）",
    )
    parser.add_argument(
        "--min-turnover", type=float, default=None,
        help="流动性硬过滤下限（元）：近 20 日均成交额低于该值直接剔除；0 = 关闭（默认）",
    )
    parser.add_argument(
        "--max-volatility", type=float, default=None,
        help="过热过滤上限：近 N 日日收益 std 高于该值直接剔除；0 = 关闭（默认，S16）",
    )
    parser.add_argument(
        "--min-price", type=float, default=None,
        help="低价过滤下限（元）：调仓日收盘价低于该值直接剔除；0 = 关闭（默认，S12）",
    )
    parser.add_argument(
        "--rebalance-freq", default="weekly", choices=["weekly", "monthly"],
        help="调仓频率：weekly = 每周最后一个交易日（默认）；monthly = 每月最后一个交易日",
    )
    parser.add_argument("--config-json", default="", help="JSON 文件路径，覆盖任意配置字段")
    parser.add_argument(
        "--data-gate-mode",
        default=None,
        choices=["", "research", "backtest", "paper", "live"],
        help="信号生成前强制运行数据门禁；实盘前建议设为 live",
    )
    parser.add_argument("--equal-weights", action="store_true", help="打分权重改为三项等权（对照实验）")
    parser.add_argument(
        "--max-industry-streak", type=int, default=None,
        help="主线月龄上限：0=不限制（默认）；1=只买连续入选第 1 个月的新晋主线",
    )
    return parser.parse_args(argv)


def build_config(args: argparse.Namespace) -> MainlineConfig:
    """把命令行参数翻译成 MainlineConfig（--config-json 先生效，命令行参数最后覆盖）。"""
    config = MainlineConfig()
    if args.config_json:
        config = load_config_from_json(args.config_json, base=config)

    config = replace(
        config,
        start=args.start,
        end=args.end,
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        name=args.name,
        universe=args.universe,
        industry_source=args.industry_source,
        industry_mode=args.industry_mode,
        rebalance_freq=args.rebalance_freq,
    )
    if args.trend_filters is not None:
        config = replace(config, trend_filters_enabled=(args.trend_filters == "on"))
    if args.score_weights:
        parts = [float(x) for x in args.score_weights.split(",")]
        if len(parts) != 3:
            raise ValueError(f'--score-weights 需要 3 个数，如 "-0.40,0.35,-0.25"，当前: {args.score_weights}')
        config = replace(config, score_weights=(parts[0], parts[1], parts[2]))
    if args.volatility_weight is not None:
        config = replace(config, volatility_weight=args.volatility_weight)
    if args.illiq_weight is not None:
        config = replace(config, illiq_weight=args.illiq_weight)
    if args.min_turnover is not None:
        config = replace(config, min_turnover_avg20=args.min_turnover)
    if args.max_volatility is not None:
        config = replace(config, max_volatility=args.max_volatility)
    if args.min_price is not None:
        config = replace(config, min_price=args.min_price)
    if args.equal_weights:
        config = replace(config, score_weights=(1 / 3, 1 / 3, 1 / 3))
    if args.max_industry_streak is not None:
        config = replace(config, max_industry_streak=args.max_industry_streak)
    if args.data_gate_mode is not None:
        config = replace(config, data_gate_mode=args.data_gate_mode)
    return config


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    try:
        config = build_config(args)
        run_mainline_signals(config)
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f"运行失败: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
