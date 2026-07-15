"""命令行入口：一键跑周频持仓的 8 组策略对照实验，输出深度对比报告。

实验矩阵（信号窗口 × 择时周期 × 附加风控，全部周频调仓，另加原月频版参照）：

    wk_fast_ma20        快信号(10/3/10)  + MA20 择时 + 无信号清仓
    wk_fast_ma10        快信号(10/3/10)  + MA10 择时 + 无信号清仓
    wk_mid_ma20         中信号(20/5/20)  + MA20 择时 + 无信号清仓
    wk_mid_ma20_stop8   中信号(20/5/20)  + MA20 择时 + 无信号清仓 + 8% 止损
    wk_mid_fresh4_ma20  中信号 + 主线新鲜度(≤4周) + MA20 择时 + 无信号清仓
    wk_mid_norisk       中信号裸跑（风控全关，对照组）
    wk_mid_ma20_no_ns   中信号 + MA20 择时，但不做无信号清仓（隔离清仓抖动）
    wk_slow_ma60        慢信号(40/10/40) + MA60 择时 + 无信号清仓
    wk_slow6020_ma60    慢信号(60/20/60，周频评估) + MA60 择时 + 无信号清仓
    monthly_ref_ma60    原月频版(60/20/60) + MA60 择时 + 无信号清仓（参照组）

产物：每组独立的信号目录与回测目录 + 一个汇总目录（comparison.md + results.json，
含年化/滚动收益/滚动夏普/逐年收益/强弱市分段等深度对比）。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import polars as pl

from vnpy.alpha.research.mainline import MainlineConfig, run_mainline_signals
from vnpy.alpha.research.mainline_backtest import BacktestConfig, run_mainline_backtest
from vnpy.alpha.research.mainline_backtest.analytics import analyze_nav

logger = logging.getLogger(__name__)

# 008 后包默认改为 none 模式 + 反向权重，本实验设计于 005 时代，
# 显式钉住当时的行为（行业层 select + 五项过滤 + 正向权重）保证结果可比
_LEGACY = {
    "industry_mode": "select",
    "trend_filters_enabled": True,
    "score_weights": (0.40, 0.35, 0.25),
}

# 信号变体：名字 -> MainlineConfig 覆盖字段
SIGNALS: dict[str, dict] = {
    "sig_fast": {"mom_window": 10, "confirm_window": 3, "breadth_window": 10, **_LEGACY},
    "sig_mid": {"mom_window": 20, "confirm_window": 5, "breadth_window": 20, **_LEGACY},
    "sig_slow": {"mom_window": 40, "confirm_window": 10, "breadth_window": 40, **_LEGACY},
    "sig_mid_fresh4": {
        "mom_window": 20, "confirm_window": 5, "breadth_window": 20,
        "max_industry_streak": 4, **_LEGACY,
    },
    "sig_slow6020": {"mom_window": 60, "confirm_window": 20, "breadth_window": 60, **_LEGACY},
    "sig_monthly_ref": {
        "rebalance_freq": "monthly",
        "mom_window": 60, "confirm_window": 20, "breadth_window": 60,
        **_LEGACY,
    },
}

# 策略变体：(组名, 信号名, BacktestConfig 覆盖字段)
STRATEGIES: list[tuple[str, str, dict]] = [
    ("wk_fast_ma20", "sig_fast", {"timing_enabled": True, "timing_ma_window": 20, "no_signal_exit_enabled": True}),
    ("wk_fast_ma10", "sig_fast", {"timing_enabled": True, "timing_ma_window": 10, "no_signal_exit_enabled": True}),
    ("wk_mid_ma20", "sig_mid", {"timing_enabled": True, "timing_ma_window": 20, "no_signal_exit_enabled": True}),
    ("wk_mid_ma20_stop8", "sig_mid", {
        "timing_enabled": True, "timing_ma_window": 20, "no_signal_exit_enabled": True,
        "stop_loss_enabled": True, "stop_loss_rate": 0.08,
    }),
    ("wk_mid_fresh4_ma20", "sig_mid_fresh4", {"timing_enabled": True, "timing_ma_window": 20, "no_signal_exit_enabled": True}),
    ("wk_mid_norisk", "sig_mid", {}),
    ("wk_slow_ma60", "sig_slow", {"timing_enabled": True, "timing_ma_window": 60, "no_signal_exit_enabled": True}),
    ("wk_slow6020_ma60", "sig_slow6020", {"timing_enabled": True, "timing_ma_window": 60, "no_signal_exit_enabled": True}),
    ("wk_mid_ma20_no_ns", "sig_mid", {"timing_enabled": True, "timing_ma_window": 20}),
    ("monthly_ref_ma60", "sig_monthly_ref", {"timing_enabled": True, "timing_ma_window": 60, "no_signal_exit_enabled": True}),
]

IN_SAMPLE_NOTE = (
    "**样本内声明**：全部参数变体都在同一段历史样本上比较，结果属于样本内结论，"
    "存在多重比较带来的过拟合风险，不能直接外推到未来；择优前应做样本外验证。"
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="周频主线策略 8 组对照实验")
    parser.add_argument("--data-dir", default="data", help="数据湖根目录")
    parser.add_argument("--signal-output-dir", default="outputs/mainline", help="信号产物根目录")
    parser.add_argument("--backtest-output-dir", default="outputs/mainline_backtest", help="回测与汇总产物根目录")
    parser.add_argument("--name", default="weekly_comparison", help="汇总目录名后缀")
    parser.add_argument("--start", default="", help="共用起始日期（含）")
    parser.add_argument("--end", default="", help="共用结束日期（含）")
    return parser.parse_args(argv)


def run_signals(args: argparse.Namespace) -> dict[str, Path]:
    """把每个信号变体各跑一遍，返回 {信号名: selection.parquet 路径}。"""
    selection_paths: dict[str, Path] = {}
    for signal_name, overrides in SIGNALS.items():
        config = replace(
            MainlineConfig(**overrides),
            data_dir=args.data_dir, output_dir=args.signal_output_dir,
            name=signal_name, start=args.start, end=args.end,
        )
        result = run_mainline_signals(config)
        selection_paths[signal_name] = result["output_dir"] / "selection.parquet"
        logger.info("信号 %-16s 完成: %s", signal_name, result["output_dir"])
    return selection_paths


def run_strategies(args: argparse.Namespace, selection_paths: dict[str, Path]) -> list[dict]:
    """逐组回测 + 深度分析，返回每组的完整结果字典。"""
    results: list[dict] = []
    for name, signal_name, overrides in STRATEGIES:
        config = BacktestConfig(
            selection_path=str(selection_paths[signal_name]),
            data_dir=args.data_dir, output_dir=args.backtest_output_dir,
            name=name, start=args.start, end=args.end, **overrides,
        )
        result = run_mainline_backtest(config)
        nav_frame = pl.read_parquet(Path(result["output_dir"]) / "nav.parquet")
        metrics = result["metrics"]
        logger.info(
            "组 %-18s 年化 %+7.2f%%  回撤 %7.2f%%  夏普 %5.2f",
            name, metrics["annual_return"] * 100, metrics["max_drawdown"] * 100, metrics["sharpe"],
        )
        results.append(
            {
                "name": name,
                "signal": signal_name,
                "signal_overrides": SIGNALS[signal_name],
                "backtest_overrides": overrides,
                "metrics": metrics,
                "analytics": analyze_nav(nav_frame),
                "output_dir": str(result["output_dir"]),
            }
        )
    return results


def fmt(value, pattern: str = "{:+.1%}") -> str:
    """None 安全的格式化（窗口不足时显示 -）。"""
    return "—" if value is None else pattern.format(value)


def build_comparison(results: list[dict]) -> str:
    """生成 comparison.md 全文：五张对比表 + 声明 + 产物路径。"""
    lines = [
        "# 周频主线策略对照实验（8 组）", "",
        f"生成时间: {datetime.now().isoformat(timespec='seconds')}；"
        "全部组共用同一数据湖，执行口径一致（次日开盘、含佣金/印花税/滑点）。", "",
        "## 1. 核心指标总表", "",
        "| 组 | 总收益 | 年化 | 最大回撤 | 夏普 | Calmar | 期胜率 | 跑赢基准率 | 年均换手 | 成本拖累 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item in results:
        m = item["metrics"]
        calmar = m["annual_return"] / abs(m["max_drawdown"]) if m["max_drawdown"] < 0 else 0.0
        lines.append(
            f"| {item['name']} | {m['total_return']:+.1%} | {m['annual_return']:+.2%} "
            f"| {m['max_drawdown']:.1%} | {m['sharpe']:.2f} | {calmar:.2f} "
            f"| {m['monthly_win_rate']:.0%} | {m['win_vs_benchmark_rate']:.0%} "
            f"| {m['annual_turnover']:.1f} | {m['cost_drag']:.2%} |"
        )

    lines += [
        "", "## 2. 滚动 12 个月收益分布（任意时点开始跟一年的体验）", "",
        "| 组 | 最差 | 25 分位 | 中位 | 75 分位 | 最好 | 为正占比 |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in results:
        r = item["analytics"]["rolling_1y_return"]
        lines.append(
            f"| {item['name']} | {fmt(r['min'])} | {fmt(r['p25'])} | {fmt(r['median'])} "
            f"| {fmt(r['p75'])} | {fmt(r['max'])} | {fmt(r['positive_share'], '{:.0%}')} |"
        )

    lines += [
        "", "## 3. 滚动 3 个月收益分布（短期体验，未年化）", "",
        "| 组 | 最差 | 中位 | 最好 | 为正占比 |", "|---|---|---|---|---|",
    ]
    for item in results:
        r = item["analytics"]["rolling_3m_return"]
        lines.append(
            f"| {item['name']} | {fmt(r['min'])} | {fmt(r['median'])} | {fmt(r['max'])} "
            f"| {fmt(r['positive_share'], '{:.0%}')} |"
        )

    lines += [
        "", "## 4. 滚动 12 个月夏普分布", "",
        "| 组 | 最差 | 中位 | 最好 | 为正占比 |", "|---|---|---|---|---|",
    ]
    for item in results:
        r = item["analytics"]["rolling_1y_sharpe"]
        lines.append(
            f"| {item['name']} | {fmt(r['min'], '{:.2f}')} | {fmt(r['median'], '{:.2f}')} "
            f"| {fmt(r['max'], '{:.2f}')} | {fmt(r['positive_share'], '{:.0%}')} |"
        )

    years = sorted({y["year"] for item in results for y in item["metrics"]["by_year"]})
    lines += ["", "## 5. 逐年收益（策略 / 沪深300）", ""]
    lines.append("| 组 | " + " | ".join(str(y) for y in years) + " |")
    lines.append("|---|" + "---|" * len(years))
    for item in results:
        by_year = {y["year"]: y for y in item["metrics"]["by_year"]}
        cells = []
        for year in years:
            y = by_year.get(year)
            cells.append(f"{y['total_return']:+.1%} / {y['benchmark_total_return']:+.1%}" if y else "—")
        lines.append(f"| {item['name']} | " + " | ".join(cells) + " |")

    lines += [
        "", "## 6. 强弱市分段（基准相对 60 日均线，日收益算术年化）", "",
        "| 组 | 强市天数 | 强市年化 | 弱市天数 | 弱市年化 | 强市基准年化 | 弱市基准年化 |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in results:
        regime = item["analytics"]["regime"]
        s, w = regime["strong"], regime["weak"]
        lines.append(
            f"| {item['name']} | {s['days']} | {fmt(s['annual_return'])} | {w['days']} "
            f"| {fmt(w['annual_return'])} | {fmt(s['benchmark_annual_return'])} "
            f"| {fmt(w['benchmark_annual_return'])} |"
        )

    lines += ["", "## 声明", "", IN_SAMPLE_NOTE, "", "## 各组产物目录", ""]
    for item in results:
        lines.append(f"- {item['name']}: `{item['output_dir']}`（信号: {item['signal']}）")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    try:
        selection_paths = run_signals(args)
        results = run_strategies(args, selection_paths)
    except (FileNotFoundError, ValueError) as error:
        print(f"对照实验失败: {error}", file=sys.stderr)
        return 1

    summary_dir = Path(args.backtest_output_dir) / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{args.name}"
    summary_dir.mkdir(parents=True, exist_ok=True)
    (summary_dir / "comparison.md").write_text(build_comparison(results), encoding="utf-8")
    (summary_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    logger.info("汇总已写入 %s", summary_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
