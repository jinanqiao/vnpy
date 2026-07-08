from pathlib import Path
from typing import Any


def save_turtle_report(
    output_path: str | Path,
    metrics: Any,
    assumptions: dict[str, str] | None = None,
    figure_paths: list[str | Path] | None = None,
) -> Path:
    """Save a beginner-friendly Markdown report for one turtle backtest."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    assumptions = assumptions or {}
    figure_paths = figure_paths or []

    lines: list[str] = [
        "# 股票版海龟策略回测报告",
        "",
        "## 策略假设",
    ]

    if assumptions:
        for key, value in assumptions.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- 未提供额外假设。")

    lines.extend([
        "",
        "## 核心指标",
        f"- 总收益: {_format_percent(metrics.total_return)}",
        f"- 年化收益: {_format_percent(metrics.annual_return)}",
        f"- 年化波动: {_format_percent(metrics.annual_volatility)}",
        f"- Sharpe: {metrics.sharpe_ratio:.2f}",
        f"- 最大回撤: {_format_percent(metrics.max_drawdown)}",
        f"- Calmar: {metrics.calmar_ratio:.2f}",
        f"- 胜率: {_format_percent(metrics.win_rate)}",
        f"- 平均换手: {_format_percent(metrics.average_turnover)}",
        "",
        "## 图表文件",
    ])

    if figure_paths:
        for figure_path in figure_paths:
            lines.append(f"- {figure_path}")
    else:
        lines.append("- 未生成图表。")

    lines.extend([
        "",
        "## 局限性",
        "- 回测结果不代表未来收益。",
        "- 第一版为 long-only 股票版海龟，尚未覆盖经典海龟的完整加仓和做空规则。",
        "- 数据质量、涨跌停、停牌和流动性约束会显著影响真实表现。",
        "",
    ])

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _format_percent(value: float) -> str:
    return f"{value * 100:.2f}%"
