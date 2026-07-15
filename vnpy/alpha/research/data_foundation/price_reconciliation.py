from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
import json

import polars as pl


@dataclass(frozen=True)
class PriceReconciliationResult:
    """QMT/AKShare 后复权价差诊断结果。"""

    status: str
    recent_days: int
    coverage: float
    coverage_min: float
    checked_rows: int
    joined_rows: int
    diff_rows: int
    diff_ratio: float
    normalized_diff_rows: int
    normalized_diff_ratio: float
    price_diff_max: float
    date_range: dict[str, str | None]
    top_symbols: list[dict[str, Any]]
    max_samples: list[dict[str, Any]]
    suspected_reasons: dict[str, int]
    classified_path: str | None = None
    unknown_symbols_path: str | None = None
    unknown_symbol_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def write_json(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return output


def diagnose_price_reconciliation(
    data_root: str | Path = "data",
    *,
    recent_days: int = 252,
    price_diff_max: float = 0.02,
    coverage_min: float = 0.99,
    max_samples: int = 20,
    write_artifacts: bool = True,
) -> PriceReconciliationResult:
    """诊断 QMT 后复权价与 AKShare 后复权价的覆盖率和差异。"""
    root = Path(data_root)
    qmt_path = root / "silver" / "daily_bars_adjusted.parquet"
    akshare_path = root / "silver" / "outstanding_share_turnover.parquet"
    if not qmt_path.exists() or not akshare_path.exists():
        return _failed_result(recent_days, coverage_min, price_diff_max, "missing_input")

    qmt = pl.read_parquet(qmt_path)
    akshare = pl.read_parquet(akshare_path)
    required_qmt = {"datetime", "vt_symbol", "close"}
    required_akshare = {"datetime", "vt_symbol", "close_hfq"}
    if not required_qmt.issubset(qmt.columns) or not required_akshare.issubset(akshare.columns):
        return _failed_result(recent_days, coverage_min, price_diff_max, "missing_columns")

    primary = _recent_window(
        qmt.select("datetime", "vt_symbol", pl.col("close").cast(pl.Float64).alias("qmt_close_adjusted")),
        recent_days,
    )
    comparison = akshare.select("datetime", "vt_symbol", pl.col("close_hfq").cast(pl.Float64).alias("akshare_close_hfq"))
    primary_keys = primary.select("datetime", "vt_symbol").unique()
    joined = (
        primary.join(comparison, on=["datetime", "vt_symbol"], how="inner")
        .with_columns(
            (pl.col("qmt_close_adjusted") - pl.col("akshare_close_hfq")).abs().alias("abs_diff"),
            (
                (pl.col("qmt_close_adjusted") - pl.col("akshare_close_hfq")).abs()
                / pl.col("qmt_close_adjusted").abs().clip(lower_bound=1.0)
            ).alias("relative_diff"),
        )
        .sort(["datetime", "vt_symbol"])
    )
    scales = (
        joined.filter((pl.col("qmt_close_adjusted").abs() > 1e-12) & (pl.col("akshare_close_hfq").abs() > 1e-12))
        .with_columns((pl.col("akshare_close_hfq") / pl.col("qmt_close_adjusted")).alias("source_scale"))
        .group_by("vt_symbol")
        .agg(pl.col("source_scale").median().alias("source_scale"))
    )
    joined = (
        joined.join(scales, on="vt_symbol", how="left")
        .with_columns(
            (pl.col("akshare_close_hfq") / pl.col("source_scale")).alias("akshare_close_normalized"),
        )
        .with_columns(
            (
                (pl.col("qmt_close_adjusted") - pl.col("akshare_close_normalized")).abs()
                / pl.col("qmt_close_adjusted").abs().clip(lower_bound=1.0)
            ).alias("normalized_relative_diff")
        )
    )
    coverage = joined.height / max(primary_keys.height, 1)
    failed = joined.filter(pl.col("relative_diff") > price_diff_max)
    diff_rows = failed.height
    diff_ratio = diff_rows / max(joined.height, 1)
    normalized_failed = joined.filter(pl.col("normalized_relative_diff") > price_diff_max)
    normalized_diff_rows = normalized_failed.height
    normalized_diff_ratio = normalized_diff_rows / max(joined.height, 1)

    failed_with_reason = _attach_reason(root, failed)
    artifact_paths = _write_classification_artifacts(root, failed_with_reason) if write_artifacts else {}
    top_symbols = _top_symbols(failed_with_reason)
    sample_rows = _max_samples(failed_with_reason, max_samples)
    reasons = _reason_counts(failed_with_reason)
    unknown_symbol_count = _unknown_symbol_count(failed_with_reason)
    date_range = _date_range(primary)
    status = "pass" if coverage >= coverage_min and normalized_diff_rows == 0 else "fail"
    return PriceReconciliationResult(
        status=status,
        recent_days=recent_days,
        coverage=round(coverage, 6),
        coverage_min=coverage_min,
        checked_rows=primary_keys.height,
        joined_rows=joined.height,
        diff_rows=diff_rows,
        diff_ratio=round(diff_ratio, 6),
        normalized_diff_rows=normalized_diff_rows,
        normalized_diff_ratio=round(normalized_diff_ratio, 6),
        price_diff_max=price_diff_max,
        date_range=date_range,
        top_symbols=top_symbols,
        max_samples=sample_rows,
        suspected_reasons=reasons,
        classified_path=artifact_paths.get("classified"),
        unknown_symbols_path=artifact_paths.get("unknown_symbols"),
        unknown_symbol_count=unknown_symbol_count,
    )


def write_price_reconciliation_report(result: PriceReconciliationResult, path: str | Path) -> Path:
    """写出 QMT/AKShare 后复权价差 Markdown 报告。"""
    lines = [
        "# QMT/AKShare 后复权价差诊断报告",
        "",
        f"- 状态: `{result.status}`",
        f"- 检查窗口: 最近 `{result.recent_days}` 个交易日",
        f"- 日期范围: `{result.date_range.get('start')}` ~ `{result.date_range.get('end')}`",
        f"- 覆盖率: `{result.coverage}`，阈值 `>= {result.coverage_min}`",
        f"- 绝对价格超阈值行数: `{result.diff_rows}`",
        f"- 绝对价格超阈值比例: `{result.diff_ratio}`",
        f"- 按股票尺度归一化后超阈值行数: `{result.normalized_diff_rows}`",
        f"- 按股票尺度归一化后超阈值比例: `{result.normalized_diff_ratio}`",
        f"- 价差阈值: `{result.price_diff_max}`",
        f"- unknown 异常股票数: `{result.unknown_symbol_count}`",
        f"- 分类明细: `{result.classified_path or ''}`",
        f"- unknown 隔离清单: `{result.unknown_symbols_path or ''}`",
        "",
        "## 疑似原因分布",
        "",
    ]
    if result.suspected_reasons:
        for reason, count in result.suspected_reasons.items():
            lines.append(f"- `{reason}`: {count}")
    else:
        lines.append("- 无")

    lines.extend(["", "## 异常最多股票", "", "| vt_symbol | diff_rows | max_relative_diff |", "|---|---:|---:|"])
    for item in result.top_symbols:
        lines.append(f"| {item['vt_symbol']} | {item['diff_rows']} | {item['max_relative_diff']} |")
    if not result.top_symbols:
        lines.append("| - | 0 | 0 |")

    lines.extend(["", "## 最大异常样本", "", "| datetime | vt_symbol | QMT | AKShare | relative_diff | reason |", "|---|---|---:|---:|---:|---|"])
    for item in result.max_samples:
        lines.append(
            f"| {item['datetime']} | {item['vt_symbol']} | {item['qmt_close_adjusted']} | "
            f"{item['akshare_close_hfq']} | {item['relative_diff']} | {item['suspected_reason']} |"
        )
    if not result.max_samples:
        lines.append("| - | - | 0 | 0 | 0 | - |")

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def _recent_window(df: pl.DataFrame, recent_days: int) -> pl.DataFrame:
    dates = df.select("datetime").unique().sort("datetime").tail(recent_days)
    return df.join(dates, on="datetime", how="inner")


def _attach_reason(root: Path, failed: pl.DataFrame) -> pl.DataFrame:
    if failed.is_empty():
        return failed.with_columns(pl.lit(None, dtype=pl.Utf8).alias("suspected_reason"))
    with_reason = failed.with_columns(
        pl.when((pl.col("akshare_close_hfq") / pl.col("qmt_close_adjusted")).abs() > 50)
        .then(pl.lit("unit_mismatch"))
        .when((pl.col("akshare_close_hfq") / pl.col("qmt_close_adjusted")).abs() < 0.02)
        .then(pl.lit("unit_mismatch"))
        .when(
            (pl.col("normalized_relative_diff") <= 0.02)
            & ((pl.col("source_scale") - 1.0).abs() > 0.02)
        )
        .then(pl.lit("unit_mismatch"))
        .otherwise(pl.lit("unknown"))
        .alias("suspected_reason")
    )
    actions_path = root / "silver" / "corporate_actions_candidates.parquet"
    if actions_path.exists():
        actions = pl.read_parquet(actions_path).select("datetime", "vt_symbol").unique()
        with_reason = (
            with_reason.join(actions.with_columns(pl.lit(True).alias("near_corporate_action")), on=["datetime", "vt_symbol"], how="left")
            .with_columns(
                pl.when(pl.col("near_corporate_action").fill_null(False))
                .then(pl.lit("corporate_action"))
                .otherwise(pl.col("suspected_reason"))
                .alias("suspected_reason")
            )
            .drop("near_corporate_action")
        )
    return with_reason


def _write_classification_artifacts(root: Path, failed: pl.DataFrame) -> dict[str, str]:
    quality_dir = root / "quality"
    quarantine_dir = root / "quarantine"
    quality_dir.mkdir(parents=True, exist_ok=True)
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    classified_path = quality_dir / "price_reconciliation_classified.parquet"
    unknown_path = quarantine_dir / "reconciliation_unknown_symbols.parquet"

    if failed.is_empty():
        pl.DataFrame(
            schema={
                "datetime": pl.Date,
                "vt_symbol": pl.String,
                "qmt_close_adjusted": pl.Float64,
                "akshare_close_hfq": pl.Float64,
                "relative_diff": pl.Float64,
                "normalized_relative_diff": pl.Float64,
                "source_scale": pl.Float64,
                "suspected_reason": pl.String,
            }
        ).write_parquet(classified_path)
        pl.DataFrame(
            schema={
                "vt_symbol": pl.String,
                "unknown_rows": pl.UInt32,
                "max_normalized_relative_diff": pl.Float64,
                "first_datetime": pl.Date,
                "last_datetime": pl.Date,
                "quarantine_reason": pl.String,
            }
        ).write_parquet(unknown_path)
        return {"classified": str(classified_path), "unknown_symbols": str(unknown_path)}

    classified = failed.select(
        pl.col("datetime").cast(pl.Date),
        "vt_symbol",
        "qmt_close_adjusted",
        "akshare_close_hfq",
        "relative_diff",
        "normalized_relative_diff",
        "source_scale",
        "suspected_reason",
    )
    classified.write_parquet(classified_path)

    unknown = (
        classified.filter(pl.col("suspected_reason") == "unknown")
        .group_by("vt_symbol")
        .agg(
            pl.len().alias("unknown_rows"),
            pl.col("normalized_relative_diff").max().alias("max_normalized_relative_diff"),
            pl.col("datetime").min().alias("first_datetime"),
            pl.col("datetime").max().alias("last_datetime"),
        )
        .with_columns(pl.lit("qmt_akshare_unknown_price_diff").alias("quarantine_reason"))
        .sort(["unknown_rows", "max_normalized_relative_diff", "vt_symbol"], descending=[True, True, False])
    )
    unknown.write_parquet(unknown_path)
    return {"classified": str(classified_path), "unknown_symbols": str(unknown_path)}


def _unknown_symbol_count(failed: pl.DataFrame) -> int:
    if failed.is_empty() or "suspected_reason" not in failed.columns:
        return 0
    return failed.filter(pl.col("suspected_reason") == "unknown").select(pl.col("vt_symbol").n_unique()).item()


def _top_symbols(failed: pl.DataFrame) -> list[dict[str, Any]]:
    if failed.is_empty():
        return []
    return (
        failed.group_by("vt_symbol")
        .agg(
            pl.len().alias("diff_rows"),
            pl.col("relative_diff").max().round(6).alias("max_relative_diff"),
        )
        .sort(["diff_rows", "max_relative_diff", "vt_symbol"], descending=[True, True, False])
        .head(20)
        .to_dicts()
    )


def _max_samples(failed: pl.DataFrame, max_samples: int) -> list[dict[str, Any]]:
    if failed.is_empty():
        return []
    return (
        failed.select(
            pl.col("datetime").cast(pl.Utf8),
            "vt_symbol",
            pl.col("qmt_close_adjusted").round(6),
            pl.col("akshare_close_hfq").round(6),
            pl.col("relative_diff").round(6),
            "suspected_reason",
        )
        .sort(["relative_diff", "datetime", "vt_symbol"], descending=[True, False, False])
        .head(max_samples)
        .to_dicts()
    )


def _reason_counts(failed: pl.DataFrame) -> dict[str, int]:
    if failed.is_empty():
        return {}
    return {
        row["suspected_reason"]: row["len"]
        for row in failed.group_by("suspected_reason").len().sort("suspected_reason").iter_rows(named=True)
    }


def _date_range(df: pl.DataFrame) -> dict[str, str | None]:
    if df.is_empty():
        return {"start": None, "end": None}
    row = df.select(pl.col("datetime").cast(pl.Date).min().alias("start"), pl.col("datetime").cast(pl.Date).max().alias("end")).row(0, named=True)
    return {"start": str(row["start"]) if row["start"] else None, "end": str(row["end"]) if row["end"] else None}


def _failed_result(recent_days: int, coverage_min: float, price_diff_max: float, reason: str) -> PriceReconciliationResult:
    return PriceReconciliationResult(
        status="fail",
        recent_days=recent_days,
        coverage=0.0,
        coverage_min=coverage_min,
        checked_rows=0,
        joined_rows=0,
        diff_rows=0,
        diff_ratio=0.0,
        normalized_diff_rows=0,
        normalized_diff_ratio=0.0,
        price_diff_max=price_diff_max,
        date_range={"start": None, "end": None},
        top_symbols=[],
        max_samples=[],
        suspected_reasons={reason: 1},
        unknown_symbol_count=1 if reason == "unknown" else 0,
    )
