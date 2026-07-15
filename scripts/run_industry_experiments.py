"""命令行入口：行业层改造的四组对照实验（006）。

回答三个问题（对应 outputs/factor_checks/ 的行业动量检验结论）：
    1. 换更细的申万一级口径（31 个行业），"选行业"会不会变准；
    2. 行业层降级为"市场状态开关"（regime 模式，有主线才建仓、全市场选股）会不会更好；
    3. 两者叠加的效果。

实验矩阵（全部月频 60/20/60 + MA60 择时 + 无信号清仓，即历史最优配置）：

    base_gics1_select   GICS1 + 行业内选股（现状参照组）
    sw1_select          申万一级 + 行业内选股（只换口径）
    gics1_regime        GICS1 + 状态开关 + 全市场选股（只换模式）
    sw1_regime          申万一级 + 状态开关 + 全市场选股（口径 + 模式）

产物：每组独立的信号目录与回测目录 + 汇总目录（comparison.md + results.json）。
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

# 共用的月频慢信号窗口（历史最优配置；008 后包默认改为 none 模式，
# 本实验比较的是行业层各形态，故显式钉住 006 时代的行为）
BASE_SIGNAL = {
    "rebalance_freq": "monthly",
    "mom_window": 60, "confirm_window": 20, "breadth_window": 60,
    "industry_mode": "select",
    "trend_filters_enabled": True,
    "score_weights": (0.40, 0.35, 0.25),
}

# (组名, MainlineConfig 覆盖字段)
VARIANTS: list[tuple[str, dict]] = [
    ("base_gics1_select", {}),
    ("sw1_select", {"industry_source": "sw1"}),
    ("gics1_regime", {"industry_mode": "regime"}),
    ("sw1_regime", {"industry_source": "sw1", "industry_mode": "regime"}),
]

# 全组共用的风控配置（历史推荐组合）
RISK = {"timing_enabled": True, "timing_ma_window": 60, "no_signal_exit_enabled": True}

IN_SAMPLE_NOTE = (
    "**样本内声明**：全部变体在同一段历史样本上比较，属样本内结论，"
    "存在多重比较的过拟合风险；行业动量因子本身未通过 Rank IC 检验"
    "（outputs/factor_checks/），任何组的超额都不应外推到未来。"
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="行业层改造四组对照实验")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--signal-output-dir", default="outputs/mainline")
    parser.add_argument("--backtest-output-dir", default="outputs/mainline_backtest")
    parser.add_argument("--name", default="industry_comparison")
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    return parser.parse_args(argv)


def run_all(args: argparse.Namespace) -> list[dict]:
    results: list[dict] = []
    for name, overrides in VARIANTS:
        signal_config = replace(
            MainlineConfig(**BASE_SIGNAL, **overrides),
            data_dir=args.data_dir, output_dir=args.signal_output_dir,
            name=f"sig_{name}", start=args.start, end=args.end,
        )
        signal_result = run_mainline_signals(signal_config)
        logger.info("信号 %-20s 完成: %s", name, signal_result["output_dir"])

        backtest_config = BacktestConfig(
            selection_path=str(signal_result["output_dir"] / "selection.parquet"),
            data_dir=args.data_dir, output_dir=args.backtest_output_dir,
            name=name, start=args.start, end=args.end, **RISK,
        )
        backtest_result = run_mainline_backtest(backtest_config)
        nav_frame = pl.read_parquet(Path(backtest_result["output_dir"]) / "nav.parquet")
        metrics = backtest_result["metrics"]
        logger.info(
            "组 %-20s 年化 %+7.2f%%  回撤 %7.2f%%  夏普 %5.2f",
            name, metrics["annual_return"] * 100, metrics["max_drawdown"] * 100, metrics["sharpe"],
        )
        results.append(
            {
                "name": name,
                "signal_overrides": {**BASE_SIGNAL, **overrides},
                "metrics": metrics,
                "analytics": analyze_nav(nav_frame),
                "signal_dir": str(signal_result["output_dir"]),
                "output_dir": str(backtest_result["output_dir"]),
            }
        )
    return results


def fmt(value, pattern: str = "{:+.1%}") -> str:
    return "—" if value is None else pattern.format(value)


def build_comparison(results: list[dict]) -> str:
    lines = [
        "# 行业层改造对照实验（GICS1/SW1 x select/regime）", "",
        f"生成时间: {datetime.now().isoformat(timespec='seconds')}；"
        "全部组共用同一数据湖与执行口径（月频 60/20/60，MA60 择时 + 无信号清仓，"
        "次日开盘成交，含佣金/印花税/滑点）。", "",
        "## 1. 核心指标总表", "",
        "| 组 | 总收益 | 年化 | 最大回撤 | 夏普 | 期胜率 | 跑赢基准率 | 年均换手 | 成本拖累 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for item in results:
        m = item["metrics"]
        lines.append(
            f"| {item['name']} | {m['total_return']:+.1%} | {m['annual_return']:+.2%} "
            f"| {m['max_drawdown']:.1%} | {m['sharpe']:.2f} "
            f"| {m['monthly_win_rate']:.0%} | {m['win_vs_benchmark_rate']:.0%} "
            f"| {m['annual_turnover']:.1f} | {m['cost_drag']:.2%} |"
        )

    lines += [
        "", "## 2. 滚动 12 个月收益分布", "",
        "| 组 | 最差 | 中位 | 最好 | 为正占比 |", "|---|---|---|---|---|",
    ]
    for item in results:
        r = item["analytics"]["rolling_1y_return"]
        lines.append(
            f"| {item['name']} | {fmt(r['min'])} | {fmt(r['median'])} | {fmt(r['max'])} "
            f"| {fmt(r['positive_share'], '{:.0%}')} |"
        )

    years = sorted({y["year"] for item in results for y in item["metrics"]["by_year"]})
    lines += ["", "## 3. 逐年收益（策略 / 沪深300）", ""]
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
        "", "## 4. 强弱市分段（基准相对 60 日均线，日收益算术年化）", "",
        "| 组 | 强市年化 | 弱市年化 | 强市基准 | 弱市基准 |", "|---|---|---|---|---|",
    ]
    for item in results:
        regime = item["analytics"]["regime"]
        s, w = regime["strong"], regime["weak"]
        lines.append(
            f"| {item['name']} | {fmt(s['annual_return'])} | {fmt(w['annual_return'])} "
            f"| {fmt(s['benchmark_annual_return'])} | {fmt(w['benchmark_annual_return'])} |"
        )

    lines += ["", "## 声明", "", IN_SAMPLE_NOTE, "", "## 各组产物目录", ""]
    for item in results:
        lines.append(f"- {item['name']}: 信号 `{item['signal_dir']}` / 回测 `{item['output_dir']}`")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args()
    try:
        results = run_all(args)
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
