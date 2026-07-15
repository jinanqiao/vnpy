"""命令行入口：一键跑四组风控对照实验，量化每条规则的边际贡献。

四组固定组合（specs/003-mainline-risk-control/data-model.md §4）：

    baseline    全关（002 现状）
    timing      仅择时
    timing_ns   择时 + 无信号清仓
    all_on      三条全开

用法示例（在仓库根目录执行）：

    python scripts/run_risk_experiments.py \
        --selection outputs/mainline/<run_id>/selection.parquet

产物：每组一个独立回测目录 + 一个汇总目录（comparison.md 对照表与边际贡献）。
参数与行为契约见 specs/003-mainline-risk-control/contracts/cli-contract.md。
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from vnpy.alpha.research.mainline_backtest import BacktestConfig, run_mainline_backtest

logger = logging.getLogger(__name__)

# (组名, 择时, 无信号清仓, 个股止损)
GROUPS: list[tuple[str, bool, bool, bool]] = [
    ("baseline", False, False, False),
    ("timing", True, False, False),
    ("timing_ns", True, True, False),
    ("all_on", True, True, True),
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="四组风控对照实验一键跑")
    parser.add_argument("--selection", required=True, help="001 产物 selection.parquet 路径")
    parser.add_argument("--data-dir", default="data", help="数据湖根目录")
    parser.add_argument("--output-dir", default="outputs/mainline_backtest", help="产物根目录")
    parser.add_argument("--name", default="risk_comparison", help="汇总目录名后缀")
    return parser.parse_args(argv)


def run_all_groups(args: argparse.Namespace) -> list[dict]:
    """串行跑四组回测，返回每组的 {name, switches, metrics, output_dir}。"""
    results: list[dict] = []
    for name, timing, no_signal, stop_loss in GROUPS:
        config = BacktestConfig(
            selection_path=args.selection,
            data_dir=args.data_dir,
            output_dir=args.output_dir,
            name=f"risk_{name}",
            timing_enabled=timing,
            no_signal_exit_enabled=no_signal,
            stop_loss_enabled=stop_loss,
        )
        result = run_mainline_backtest(config)
        metrics = result["metrics"]
        logger.info(
            "组 %-9s 完成: 年化 %.2f%%, 最大回撤 %.2f%%, 夏普 %.2f",
            name, metrics["annual_return"] * 100, metrics["max_drawdown"] * 100, metrics["sharpe"],
        )
        results.append(
            {
                "name": name,
                "switches": {"timing": timing, "no_signal_exit": no_signal, "stop_loss": stop_loss},
                "metrics": metrics,
                "output_dir": str(result["output_dir"]),
            }
        )
    return results


def build_comparison(results: list[dict]) -> str:
    """生成 comparison.md 全文：四组对照表 + 边际贡献 + 产物路径。"""
    def onoff(flag: bool) -> str:
        return "开" if flag else "关"

    lines = [
        "# 风控对照实验汇总", "",
        "四组组合共用同一份信号与行情，只有风控开关不同。", "",
        "## 指标对照", "",
        "| 组 | 择时 | 无信号清仓 | 止损 | 总收益 | 年化 | 最大回撤 | 夏普 | 月度胜率 | 年均换手 | 成本拖累 |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in results:
        metrics = item["metrics"]
        switches = item["switches"]
        lines.append(
            f"| {item['name']} | {onoff(switches['timing'])} | {onoff(switches['no_signal_exit'])} "
            f"| {onoff(switches['stop_loss'])} | {metrics['total_return']:.2%} "
            f"| {metrics['annual_return']:.2%} | {metrics['max_drawdown']:.2%} "
            f"| {metrics['sharpe']:.2f} | {metrics['monthly_win_rate']:.1%} "
            f"| {metrics['annual_turnover']:.1f} | {metrics['cost_drag']:.2%} |"
        )

    lines += ["", "## 边际贡献（相邻组对比）", ""]
    for prev, curr, label in [
        (0, 1, "择时（timing − baseline）"),
        (1, 2, "无信号清仓（timing_ns − timing）"),
        (2, 3, "个股止损（all_on − timing_ns）"),
    ]:
        m_prev, m_curr = results[prev]["metrics"], results[curr]["metrics"]
        lines.append(
            f"- {label}: 年化 {m_curr['annual_return'] - m_prev['annual_return']:+.2%}, "
            f"最大回撤 {m_curr['max_drawdown'] - m_prev['max_drawdown']:+.2%}, "
            f"换手 {m_curr['annual_turnover'] - m_prev['annual_turnover']:+.1f}"
        )

    lines += ["", "## 各组产物目录", ""]
    for item in results:
        lines.append(f"- {item['name']}: `{item['output_dir']}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    try:
        results = run_all_groups(args)
    except (FileNotFoundError, ValueError) as error:
        print(f"对照实验失败: {error}", file=sys.stderr)
        return 1

    summary_dir = Path(args.output_dir) / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{args.name}"
    summary_dir.mkdir(parents=True, exist_ok=True)
    (summary_dir / "comparison.md").write_text(build_comparison(results), encoding="utf-8")
    logger.info("汇总已写入 %s", summary_dir / "comparison.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
