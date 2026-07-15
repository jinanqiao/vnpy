"""流水线编排：把数据加载、行业层、个股层串成一条线，并把结果落盘。

这个文件回答一个问题：一次完整的信号生成是怎么跑起来的。

run_mainline_signals() 从上到下就是策略的完整执行顺序：
    读数据 → 生成调仓日列表 → 逐个调仓日（选行业 → 选股票）→ 汇总 → 写产物。

每次运行在 outputs/mainline/<run_id>/ 下产出六个文件（对照 contracts/artifacts-schema.md）：
    config.json               参数快照（这次运行用了什么参数、什么数据）
    industry_signals.parquet  行业信号快照（逐期 × 全部行业）
    stock_signals.parquet     个股信号快照（逐期 × 主线行业内全部个股，含被剔除者）
    selection.parquet         最终入选清单
    data_quality.json         数据质量日志
    report.md                 人读摘要
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import polars as pl

from vnpy.alpha.research.data_foundation import validate_data_context
from vnpy.alpha.research.data_foundation.metadata import load_current_data_version_context

from .config import MainlineConfig
from .data_loader import (
    build_rebalance_dates,
    load_daily_bars,
    load_execution_universe,
    load_industry_map,
)
from .industry import SNAPSHOT_COLUMNS, apply_freshness, calc_industry_snapshot
from .regime import calc_stock_snapshot_regime
from .stocks import STOCK_SNAPSHOT_COLUMNS, calc_stock_snapshot

logger = logging.getLogger(__name__)

# 每份 report.md 末尾固定携带的局限性声明（R2 / R3 的已知数据缺陷）
LIMITATIONS_NOTE = """## 已知局限（解读结果前必读）

1. **行业成分是当前时点快照**：历史上改换行业的股票会被按今天的行业归属回填，行业动量存在少量偏差。
2. **价格未复权**：分红除权日会出现假性大跌，动量/均线/新高信号在除权日附近可能短暂失真；
   疑似除权样本已记录在 data_quality.json 中。
3. **主线新鲜度规则是样本内结论**：max_industry_streak 过滤规则源于对同一段历史样本的
   事后分析，存在过拟合风险，正式结论以对照回测（run_freshness_experiments.py）为准。
4. **行业动量的横截面预测力未获验证**：Rank IC / 分层检验（outputs/factor_checks/）显示
   行业动量在近 5 年的周/月持有期上无稳定预测力；行业层的价值主要在"有没有主线"这个
   状态信号（industry_mode=regime 模式即基于此），"选哪个行业"本身不可依赖。
5. 本结果仅为研究学习用途，不构成投资建议。
"""


def run_mainline_signals(config: MainlineConfig) -> dict:
    """执行完整的信号生成流程，返回三张结果表和产物目录路径。

    返回字典的键: industry_signals / stock_signals / selection（polars DataFrame），
    output_dir（Path）。同样的输入数据和参数，输出保证完全一致（可复现）。
    """
    quality_logs: list[dict] = []

    if config.data_gate_mode:
        context, gate = validate_data_context(config.data_dir, mode=config.data_gate_mode, as_of=config.end or None)
        quality_logs.append({"type": "data_context", "detail": context.to_dict()})
        if gate.status == "warning":
            quality_logs.append({"type": "data_gate_warning", "detail": gate.to_dict()})

    # 012: exclude_st 字段暂未落地（仓库无股票名快照），启用时打印警告告知未生效
    if config.exclude_st:
        logger.warning("exclude_st=True 已开启但当前实现未落地（缺股票名数据），本次运行 ST 未被剔除")

    logger.info("加载数据湖 ...")
    bars = load_daily_bars(config, quality_logs)
    industry_map = load_industry_map(config, quality_logs)
    execution_universe = load_execution_universe(config)
    rebalance_dates = build_rebalance_dates(config, bars)
    logger.info("数据就绪: %d 只股票, %d 个调仓日", bars["vt_symbol"].n_unique(), len(rebalance_dates))

    industry_frames: list[pl.DataFrame] = []
    stock_frames: list[pl.DataFrame] = []
    prev_streak: dict[str, int] = {}  # 上一期原始主线行业的月龄（004 新鲜度状态）

    for rebalance_date in rebalance_dates:
        # 008: industry_mode="none" 时不要行业层——每个调仓日直接全市场过滤打分选股
        if config.industry_mode == "none":
            stock_snapshot = calc_stock_snapshot_regime(
                bars, industry_map, execution_universe, config, rebalance_date
            )
            if stock_snapshot.is_empty():
                quality_logs.append(
                    {
                        "type": "insufficient_history_for_window",
                        "datetime": str(rebalance_date),
                        "detail": f"调仓日之前的交易日不足 {config.mom_window}+1 个，跳过该期",
                    }
                )
                logger.info("%s: 历史窗口不足，跳过", rebalance_date)
                continue
            stock_frames.append(stock_snapshot)
            selected_count = int(stock_snapshot.get_column("selected").sum())
            logger.info("%s: 全市场选股（无行业层），入选 %d 只", rebalance_date, selected_count)
            continue

        industry_snapshot = calc_industry_snapshot(bars, industry_map, config, rebalance_date)
        if industry_snapshot.is_empty():
            quality_logs.append(
                {
                    "type": "insufficient_history_for_window",
                    "datetime": str(rebalance_date),
                    "detail": f"调仓日之前的交易日不足 {config.mom_window}+1 个，跳过该期",
                }
            )
            logger.info("%s: 历史窗口不足，跳过", rebalance_date)
            continue

        # 004: 标注主线月龄并过滤老主线（max_industry_streak=0 时只标注不过滤）
        industry_snapshot, prev_streak, stale_industries = apply_freshness(
            industry_snapshot, prev_streak, config
        )
        for industry_name, streak in stale_industries:
            quality_logs.append(
                {
                    "type": "stale_industry_filtered",
                    "datetime": str(rebalance_date),
                    "industry": industry_name,
                    "streak": streak,
                    "detail": f"连续入选第 {streak} 个月，超过上限 {config.max_industry_streak}，整期不建仓",
                }
            )
            logger.info("%s: 行业 %s 连任第 %d 个月，被新鲜度规则过滤", rebalance_date, industry_name, streak)
        industry_frames.append(industry_snapshot)

        mainline = industry_snapshot.filter(pl.col("selected")).get_column("industry").to_list()
        if not mainline:
            quality_logs.append(
                {
                    "type": "no_mainline_industry",
                    "datetime": str(rebalance_date),
                    "detail": "没有行业同时满足动量门槛和广度确认，本期空清单",
                }
            )
            logger.info("%s: 无主线行业，空清单", rebalance_date)
            continue

        # regime 模式：行业层只当"市场状态开关"（有主线才走到这里），
        # 选股在全市场做；select 模式：只在主线行业内选股。
        if config.industry_mode == "regime":
            stock_snapshot = calc_stock_snapshot_regime(
                bars, industry_map, execution_universe, config, rebalance_date
            )
        else:
            stock_snapshot = calc_stock_snapshot(
                bars, industry_map, execution_universe, industry_snapshot, config, rebalance_date
            )
        stock_frames.append(stock_snapshot)

        selected_count = int(stock_snapshot.get_column("selected").sum())
        logger.info("%s: 主线行业 %s, 入选 %d 只", rebalance_date, mainline, selected_count)

        # 行业入选个股不足下限时记录（不硬凑，FR-011 / Edge Case；regime 模式全池统一取 N，不适用）
        if config.industry_mode == "select":
            per_industry = (
                stock_snapshot.filter(pl.col("selected")).group_by("industry").len().rows()
            )
            for industry_name, count in sorted(per_industry):
                if count < config.stocks_per_industry_min:
                    quality_logs.append(
                        {
                            "type": "industry_below_min_stocks",
                            "datetime": str(rebalance_date),
                            "industry": industry_name,
                            "detail": f"合格个股仅 {count} 只，低于下限 {config.stocks_per_industry_min}，按实际数量纳入",
                        }
                    )

    industry_signals = _concat_or_empty(industry_frames, SNAPSHOT_COLUMNS).sort(["rebalance_date", "industry"])
    stock_signals = _concat_or_empty(stock_frames, STOCK_SNAPSHOT_COLUMNS).sort(
        ["rebalance_date", "industry", "vt_symbol"]
    )
    selection = (
        stock_signals.filter(pl.col("selected"))
        .select(["rebalance_date", "vt_symbol", "industry", "score", "industry_rank"])
        .join(  # 004: 带上所属行业的主线月龄，方便审计新鲜度过滤
            industry_signals.select(["rebalance_date", "industry", "industry_streak"]),
            on=["rebalance_date", "industry"],
            how="left",
        )
        .sort(["rebalance_date", "industry", "industry_rank"])
    )

    output_dir = _write_artifacts(config, bars, industry_signals, stock_signals, selection, quality_logs)
    logger.info("完成: 产物已写入 %s", output_dir)

    return {
        "industry_signals": industry_signals,
        "stock_signals": stock_signals,
        "selection": selection,
        "output_dir": output_dir,
    }


def _concat_or_empty(frames: list[pl.DataFrame], schema: dict) -> pl.DataFrame:
    """把逐期结果拼成一张表；一期都没有时返回带正确列结构的空表。"""
    if not frames:
        return pl.DataFrame(schema=schema)
    return pl.concat(frames)


def _write_artifacts(
    config: MainlineConfig,
    bars: pl.DataFrame,
    industry_signals: pl.DataFrame,
    stock_signals: pl.DataFrame,
    selection: pl.DataFrame,
    quality_logs: list[dict],
) -> Path:
    """把六件套产物写进 outputs/mainline/<run_id>/ 目录。"""
    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{config.name}"
    output_dir = Path(config.output_dir) / run_id
    output_dir.mkdir(parents=True, exist_ok=False)

    config_snapshot = {
        "config": config.to_dict(),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "data_version": load_current_data_version_context(config.data_dir),
        "data_rows": {"daily_bars": bars.height},
        "data_range": {
            "first_bar": str(bars.get_column("datetime").min()),
            "last_bar": str(bars.get_column("datetime").max()),
        },
    }
    (output_dir / "config.json").write_text(
        json.dumps(config_snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    industry_signals.write_parquet(output_dir / "industry_signals.parquet")
    stock_signals.write_parquet(output_dir / "stock_signals.parquet")
    selection.write_parquet(output_dir / "selection.parquet")

    (output_dir / "data_quality.json").write_text(
        json.dumps(quality_logs, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "report.md").write_text(
        _build_report(industry_signals, selection), encoding="utf-8"
    )
    return output_dir


def _build_report(industry_signals: pl.DataFrame, selection: pl.DataFrame) -> str:
    """生成人读摘要 report.md：逐期主线行业与入选数量 + 固定局限性声明。"""
    lines = ["# 主线强势股信号报告", ""]

    if industry_signals.is_empty() and not selection.is_empty():
        # 008: industry_mode="none" 时没有行业层，按调仓日列入选数量
        lines.append("行业层已关闭（industry_mode=none），逐期全市场选股：")
        lines.append("")
        lines.append("| 调仓日 | 入选个股数 |")
        lines.append("|---|---|")
        for rebalance_date in selection.get_column("rebalance_date").unique().sort().to_list():
            count = selection.filter(pl.col("rebalance_date") == rebalance_date).height
            lines.append(f"| {rebalance_date} | {count} |")
    elif industry_signals.is_empty():
        lines.append("（本次运行没有产生任何一期信号，请检查数据范围与参数）")
    else:
        lines.append("| 调仓日 | 主线行业 | 新鲜度过滤 | 入选个股数 |")
        lines.append("|---|---|---|---|")
        for rebalance_date in industry_signals.get_column("rebalance_date").unique().sort().to_list():
            period = industry_signals.filter(pl.col("rebalance_date") == rebalance_date)
            mainline = period.filter(pl.col("selected")).get_column("industry").to_list()
            stale = period.filter(pl.col("reject_reason") == "stale").select(["industry", "industry_streak"]).rows()
            count = selection.filter(pl.col("rebalance_date") == rebalance_date).height
            mainline_text = "、".join(mainline) if mainline else "（无主线，空仓）"
            stale_text = "、".join(f"{name}(第{streak}月)" for name, streak in stale) if stale else "—"
            lines.append(f"| {rebalance_date} | {mainline_text} | {stale_text} | {count} |")

    lines += ["", LIMITATIONS_NOTE]
    return "\n".join(lines)
