"""结果落盘与人读报告生成。

这个文件回答一个问题：怎么把回测结果写下来、把指标数字讲成人能看懂的成绩单。
write_artifacts() 负责六件套落盘；build_report() 负责 report.md 全文：
核心指标表（对比沪深300）、分年度表现、风控动作汇总、异常交易汇总、
四条固定局限性声明（对应 contracts/artifacts-schema.md 的要求）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import polars as pl

from vnpy.alpha.research.data_foundation.metadata import load_current_data_version_context

from .config import BacktestConfig

LIMITATIONS_NOTE = """## 已知局限（解读结果前必读）

1. 分红按后复权收益隐含"自动再投资"处理，未建模现金分红到账时滞。
2. 一字板用日线 开=高=低=收 近似识别，盘中打开的涨跌停无法识别（结果略保守）。
3. 基准为沪深300 价格指数（不含分红），组合含分红再投资，超额收益对组合略有利。
4. 研究用途，不构成投资建议。
"""


def write_artifacts(
    config: BacktestConfig,
    nav_frame: pl.DataFrame,
    positions: pl.DataFrame,
    trades: pl.DataFrame,
    metrics: dict,
    quality_logs: list[dict],
    risk_summary: dict | None = None,
) -> Path:
    """把六件套产物写进 outputs/mainline_backtest/<run_id>/ 目录，返回目录路径。"""
    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{config.name}"
    output_dir = Path(config.output_dir) / run_id
    suffix = 2
    while output_dir.exists():   # 同一秒内连跑两次时避免目录撞车
        output_dir = Path(config.output_dir) / f"{run_id}_{suffix}"
        suffix += 1
    output_dir.mkdir(parents=True)

    selection_bytes = Path(config.selection_path).read_bytes()
    config_snapshot = {
        "config": config.to_dict(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data_version": load_current_data_version_context(config.data_dir),
        "selection_sha256": hashlib.sha256(selection_bytes).hexdigest(),
        "nav_days": nav_frame.height,
        "trades_count": trades.height,
    }
    (output_dir / "config.json").write_text(
        json.dumps(config_snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    nav_frame.write_parquet(output_dir / "nav.parquet")
    positions.write_parquet(output_dir / "positions.parquet")
    trades.write_parquet(output_dir / "trades.parquet")
    (output_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "data_quality.json").write_text(
        json.dumps(quality_logs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "report.md").write_text(
        build_report(metrics, trades, risk_summary), encoding="utf-8"
    )
    return output_dir


def build_report(metrics: dict, trades: pl.DataFrame, risk_summary: dict | None = None) -> str:
    """生成 report.md 全文：核心指标 + 分年度 + 成本 + 异常交易汇总。"""
    lines = ["# 主线强势股策略回测报告", "", "## 核心指标", ""]
    lines += [
        "| 指标 | 组合 | 沪深300 |",
        "|---|---|---|",
        f"| 总收益 | {metrics['total_return']:.2%} | {metrics['benchmark']['total_return']:.2%} |",
        f"| 年化收益 | {metrics['annual_return']:.2%} | {metrics['benchmark']['annual_return']:.2%} |",
        f"| 最大回撤 | {metrics['max_drawdown']:.2%} | {metrics['benchmark']['max_drawdown']:.2%} |",
        f"| 夏普比率 | {metrics['sharpe']:.2f} | {metrics['benchmark']['sharpe']:.2f} |",
        "",
        f"- 月度胜率 {metrics['monthly_win_rate']:.1%}，跑赢基准胜率 {metrics['win_vs_benchmark_rate']:.1%}（共 {metrics['n_periods']} 期）",
        f"- 年均换手率 {metrics['annual_turnover']:.1f} 倍；成本拖累约 {metrics['cost_drag']:.2%}/年"
        f"（零成本年化 {metrics['gross']['annual_return']:.2%}）",
        "",
        "## 分年度表现",
        "",
        "| 年份 | 组合收益 | 基准收益 | 最大回撤 |",
        "|---|---|---|---|",
    ]
    for year in metrics["by_year"]:
        lines.append(
            f"| {year['year']} | {year['total_return']:.2%} | "
            f"{year['benchmark_total_return']:.2%} | {year['max_drawdown']:.2%} |"
        )

    lines += ["", _risk_section(risk_summary), "", _abnormal_trades_section(trades), "", LIMITATIONS_NOTE]
    return "\n".join(lines)


def _risk_section(risk_summary: dict | None) -> str:
    """风控动作汇总段：三条规则各自的启用状态与触发次数。"""
    if not risk_summary:
        return "## 风控动作汇总\n\n- 风控层未启用（002 基线行为）"

    def line(enabled: bool, text: str) -> str:
        return text if enabled else text.split("：")[0] + "：未启用"

    return "\n".join(
        [
            "## 风控动作汇总",
            "",
            "- " + line(risk_summary["timing_enabled"],
                        f"大盘择时：跳过 {risk_summary['timing_skips']} 个调仓期"),
            "- " + line(risk_summary.get("timing_daily_enabled", False),
                        f"逐日择时清仓：执行 {risk_summary.get('timing_daily_exits', 0)} 次"),
            "- " + line(risk_summary.get("timing_reentry_enabled", False),
                        f"逐日再入场：执行 {risk_summary.get('timing_reentries', 0)} 次"),
            "- " + line(risk_summary["no_signal_exit_enabled"],
                        f"无信号月清仓：执行 {risk_summary['no_signal_exits']} 次"),
            "- " + line(risk_summary["stop_loss_enabled"],
                        f"个股止损：触发 {risk_summary['stop_loss_trades']} 笔"),
        ]
    )


def _abnormal_trades_section(trades: pl.DataFrame) -> str:
    """异常交易汇总段：放弃买入 / 延迟卖出 / 强制清仓的笔数与明细。"""
    abandoned = trades.filter(pl.col("status") == "abandoned")
    deferred = trades.filter(pl.col("status") == "deferred")
    forced = trades.filter(pl.col("status") == "forced")

    lines = [
        "## 异常交易汇总",
        "",
        f"- 放弃买入 {abandoned.height} 笔（执行日停牌/一字涨停/资金不足一手）",
        f"- 延迟卖出 {deferred.height} 笔（执行日停牌/一字跌停，顺延到可交易日）",
        f"- 强制清仓 {forced.height} 笔（行情永久消失，疑似退市）",
    ]
    for row in deferred.head(20).iter_rows(named=True):
        lines.append(
            f"  - 延迟 {row['executed_date']} {row['vt_symbol']}（计划 {row['planned_date']}，"
            f"延迟 {row['defer_days']} 天）"
        )
    for row in forced.head(20).iter_rows(named=True):
        lines.append(f"  - 强平 {row['executed_date']} {row['vt_symbol']}（{row['industry']}）")
    return "\n".join(lines)
