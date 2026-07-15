"""命令行入口：一键跑主线新鲜度对照实验（baseline vs fresh）。

两组只差一个参数（specs/004-mainline-freshness/contracts/cli-contract.md）：

    baseline    max_industry_streak=0（不限制，001 现状信号）
    fresh       max_industry_streak=1（只买连续入选第 1 个月的新晋主线）

每组：生成信号 → 用推荐风控（择时 + 无信号清仓，止损关）回测。

用法示例（在仓库根目录执行）：

    python scripts/run_freshness_experiments.py --name freshness_v1

产物：两组信号目录 + 两组回测目录 + 一个汇总目录（comparison.md）。
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from vnpy.alpha.research.mainline import (
    MainlineConfig,
    load_config_from_json,
    run_mainline_signals,
)
from vnpy.alpha.research.mainline_backtest import BacktestConfig, run_mainline_backtest

logger = logging.getLogger(__name__)

# (组名, 主线月龄上限)
GROUPS: list[tuple[str, int]] = [
    ("fresh_baseline", 0),
    ("fresh_only", 1),
]

# 固定携带的数据窥探声明（R7）
IN_SAMPLE_NOTE = (
    "**样本内声明**：新鲜度规则（只买新晋主线）源于对同一段历史样本的事后分析，"
    "存在过拟合风险；本对照结果仍属样本内结论，不能直接外推到未来。"
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="主线新鲜度对照实验一键跑")
    parser.add_argument("--data-dir", default="data", help="数据湖根目录")
    parser.add_argument("--signal-output-dir", default="outputs/mainline", help="信号产物根目录")
    parser.add_argument(
        "--backtest-output-dir", default="outputs/mainline_backtest",
        help="回测与汇总产物根目录",
    )
    parser.add_argument("--name", default="freshness_comparison", help="汇总目录名后缀")
    parser.add_argument("--start", default="", help="信号与回测共用的起始日期（含）")
    parser.add_argument("--end", default="", help="信号与回测共用的结束日期（含）")
    parser.add_argument(
        "--signal-config-json", default="",
        help="JSON 文件路径，覆盖信号层任意配置字段（合成数据测试/小窗口实验用）",
    )
    return parser.parse_args(argv)


def run_group(args: argparse.Namespace, group_name: str, max_streak: int) -> dict:
    """跑一组：信号生成 → 推荐风控回测，返回该组的指标与产物路径。"""
    # 008 后包默认改为 none 模式，新鲜度实验只在行业层 select 模式下有意义，钉住旧行为
    signal_config = replace(
        MainlineConfig(),
        industry_mode="select",
        trend_filters_enabled=True,
        score_weights=(0.40, 0.35, 0.25),
    )
    if args.signal_config_json:
        signal_config = load_config_from_json(args.signal_config_json, base=signal_config)
    signal_config = replace(
        signal_config,
        data_dir=args.data_dir,
        output_dir=args.signal_output_dir,
        name=group_name,
        start=args.start,
        end=args.end,
        max_industry_streak=max_streak,
    )
    signal_result = run_mainline_signals(signal_config)
    selection = signal_result["selection"]
    signal_months = selection.get_column("rebalance_date").n_unique() if selection.height else 0

    backtest_config = BacktestConfig(
        selection_path=str(signal_result["output_dir"] / "selection.parquet"),
        data_dir=args.data_dir,
        output_dir=args.backtest_output_dir,
        name=group_name,
        timing_enabled=True,          # 推荐风控组合（003 验收结论）
        no_signal_exit_enabled=True,
        stop_loss_enabled=False,
        start=args.start,
        end=args.end,
    )
    backtest_result = run_mainline_backtest(backtest_config)
    metrics = backtest_result["metrics"]
    logger.info(
        "组 %-14s 完成: 年化 %.2f%%, 最大回撤 %.2f%%, 夏普 %.2f, 有信号月 %d",
        group_name, metrics["annual_return"] * 100, metrics["max_drawdown"] * 100,
        metrics["sharpe"], signal_months,
    )
    return {
        "name": group_name,
        "max_industry_streak": max_streak,
        "signal_months": signal_months,
        "metrics": metrics,
        "signal_dir": str(signal_result["output_dir"]),
        "backtest_dir": str(backtest_result["output_dir"]),
    }


def build_comparison(results: list[dict]) -> str:
    """生成 comparison.md 全文：两组对照表 + 差异 + 样本内声明 + 产物路径。"""
    lines = [
        "# 主线新鲜度对照实验汇总", "",
        "两组共用同一数据湖与推荐风控（择时 + 无信号清仓，止损关），只差 max_industry_streak。", "",
        "## 指标对照", "",
        "| 组 | 月龄上限 | 总收益 | 年化 | 最大回撤 | 夏普 | 月度胜率 | 年均换手 | 成本拖累 | 有信号月数 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in results:
        metrics = item["metrics"]
        cap = "不限制" if item["max_industry_streak"] == 0 else str(item["max_industry_streak"])
        lines.append(
            f"| {item['name']} | {cap} | {metrics['total_return']:.2%} "
            f"| {metrics['annual_return']:.2%} | {metrics['max_drawdown']:.2%} "
            f"| {metrics['sharpe']:.2f} | {metrics['monthly_win_rate']:.1%} "
            f"| {metrics['annual_turnover']:.1f} | {metrics['cost_drag']:.2%} "
            f"| {item['signal_months']} |"
        )

    base, fresh = results[0]["metrics"], results[1]["metrics"]
    lines += [
        "", "## 差异（fresh − baseline）", "",
        f"- 年化: {fresh['annual_return'] - base['annual_return']:+.2%}",
        f"- 最大回撤: {fresh['max_drawdown'] - base['max_drawdown']:+.2%}",
        f"- 夏普: {fresh['sharpe'] - base['sharpe']:+.2f}",
        f"- 年均换手: {fresh['annual_turnover'] - base['annual_turnover']:+.1f}",
        f"- 有信号月数: {results[1]['signal_months'] - results[0]['signal_months']:+d}",
        "", "## 声明", "", IN_SAMPLE_NOTE,
        "", "## 各组产物目录", "",
    ]
    for item in results:
        lines.append(f"- {item['name']} 信号: `{item['signal_dir']}`")
        lines.append(f"- {item['name']} 回测: `{item['backtest_dir']}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    try:
        results = [run_group(args, name, max_streak) for name, max_streak in GROUPS]
    except (FileNotFoundError, ValueError) as error:
        print(f"对照实验失败: {error}", file=sys.stderr)
        return 1

    summary_dir = Path(args.backtest_output_dir) / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{args.name}"
    summary_dir.mkdir(parents=True, exist_ok=True)
    (summary_dir / "comparison.md").write_text(build_comparison(results), encoding="utf-8")
    logger.info("汇总已写入 %s", summary_dir / "comparison.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
