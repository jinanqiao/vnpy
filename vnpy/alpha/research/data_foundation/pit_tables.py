from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import json

import polars as pl


@dataclass(frozen=True)
class PitTablesBuildResult:
    """Result of building derived point-in-time helper tables."""

    data_root: str
    written: list[str]
    skipped: list[str]
    warnings: list[str]
    summary_path: str
    report_path: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_pit_tables(
    data_root: str | Path = "data",
    *,
    factor_change_threshold: float = 0.001,
) -> PitTablesBuildResult:
    """Build additive PIT helper tables from currently available local data.

    These tables are intentionally marked as derived or snapshot-based. They are
    useful for research plumbing now, but official QMT/AKShare history should
    replace the approximation columns before live trading depends on them.
    """
    root = Path(data_root)
    silver = root / "silver"
    quality = root / "quality"
    silver.mkdir(parents=True, exist_ok=True)
    quality.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    skipped: list[str] = []
    warnings: list[str] = []

    raw_path = silver / "daily_bars_raw_price.parquet"
    adjusted_path = silver / "daily_bars_adjusted.parquet"
    instrument_path = silver / "instrument_master_snapshot.parquet"
    daily_status_path = _first_existing_path(
        root,
        [
            "silver/daily_status.parquet",
            "universe/daily_status.parquet",
        ],
    )

    raw = _read_parquet_or_empty(raw_path, warnings)
    adjusted = _read_parquet_or_empty(adjusted_path, warnings)
    instruments = _read_parquet_or_empty(instrument_path, warnings)
    daily_status = _read_parquet_or_empty(daily_status_path, warnings) if daily_status_path else pl.DataFrame()

    adjust_factors = _build_adjust_factors(raw, adjusted, warnings)
    _write_dataset(adjust_factors, silver / "adjust_factors.parquet", written, skipped)

    corporate_actions = _build_corporate_action_candidates(adjust_factors, factor_change_threshold)
    _write_dataset(corporate_actions, silver / "corporate_actions_candidates.parquet", written, skipped)

    limit_status = _build_limit_status(raw, instruments, warnings)
    _write_dataset(limit_status, silver / "limit_status_daily.parquet", written, skipped)

    suspension = _build_suspension_daily(raw, daily_status, warnings)
    _write_dataset(suspension, silver / "suspension_daily.parquet", written, skipped)

    st_status = _build_st_status_daily(raw, instruments, daily_status, warnings)
    _write_dataset(st_status, silver / "st_status_daily.parquet", written, skipped)

    result = PitTablesBuildResult(
        data_root=str(root),
        written=written,
        skipped=skipped,
        warnings=warnings,
        summary_path=str(quality / "pit_tables_summary.json"),
        report_path=str(quality / "pit_tables_report.md"),
    )
    _write_summary(result)
    _write_report(result, root)
    return result


def _build_adjust_factors(raw: pl.DataFrame, adjusted: pl.DataFrame, warnings: list[str]) -> pl.DataFrame:
    schema = {
        "datetime": pl.Date,
        "vt_symbol": pl.Utf8,
        "raw_close": pl.Float64,
        "adjusted_close": pl.Float64,
        "adjust_factor": pl.Float64,
        "source": pl.Utf8,
        "pit_status": pl.Utf8,
    }
    required = {"datetime", "vt_symbol", "close"}
    if not required.issubset(raw.columns) or not required.issubset(adjusted.columns):
        warnings.append("adjust_factors: 缺少 raw/adjusted 日线必要字段，写出空表。")
        return pl.DataFrame(schema=schema)

    factors = (
        raw.select(
            "datetime",
            "vt_symbol",
            pl.col("close").cast(pl.Float64).alias("raw_close"),
        )
        .join(
            adjusted.select(
                "datetime",
                "vt_symbol",
                pl.col("close").cast(pl.Float64).alias("adjusted_close"),
            ),
            on=["datetime", "vt_symbol"],
            how="inner",
        )
        .filter(pl.col("raw_close").is_not_null() & (pl.col("raw_close") != 0))
        .with_columns(
            (pl.col("adjusted_close") / pl.col("raw_close")).alias("adjust_factor"),
            pl.lit("derived_qmt_adjusted_vs_raw").alias("source"),
            pl.lit("derived_from_adjusted_bars").alias("pit_status"),
        )
        .select(list(schema))
        .sort(["vt_symbol", "datetime"])
    )
    if factors.is_empty():
        warnings.append("adjust_factors: raw 与 adjusted 日线没有可连接的交集。")
    return factors


def _build_corporate_action_candidates(adjust_factors: pl.DataFrame, threshold: float) -> pl.DataFrame:
    schema = {
        "datetime": pl.Date,
        "vt_symbol": pl.Utf8,
        "previous_factor": pl.Float64,
        "adjust_factor": pl.Float64,
        "factor_change": pl.Float64,
        "source": pl.Utf8,
        "pit_status": pl.Utf8,
    }
    if adjust_factors.is_empty() or not {"datetime", "vt_symbol", "adjust_factor"}.issubset(adjust_factors.columns):
        return pl.DataFrame(schema=schema)

    return (
        adjust_factors.select("datetime", "vt_symbol", "adjust_factor")
        .sort(["vt_symbol", "datetime"])
        .with_columns(pl.col("adjust_factor").shift(1).over("vt_symbol").alias("previous_factor"))
        .with_columns((pl.col("adjust_factor") / pl.col("previous_factor") - 1).alias("factor_change"))
        .filter(pl.col("previous_factor").is_not_null() & (pl.col("factor_change").abs() >= threshold))
        .with_columns(
            pl.lit("derived_from_adjust_factor_change").alias("source"),
            pl.lit("candidate_from_factor_change").alias("pit_status"),
        )
        .select(list(schema))
    )


def _build_limit_status(raw: pl.DataFrame, instruments: pl.DataFrame, warnings: list[str]) -> pl.DataFrame:
    schema = {
        "datetime": pl.Date,
        "vt_symbol": pl.Utf8,
        "close": pl.Float64,
        "limit_up_snapshot": pl.Float64,
        "limit_down_snapshot": pl.Float64,
        "is_limit_up": pl.Boolean,
        "is_limit_down": pl.Boolean,
        "source": pl.Utf8,
        "pit_status": pl.Utf8,
    }
    if not {"datetime", "vt_symbol", "close"}.issubset(raw.columns):
        warnings.append("limit_status_daily: 缺少 raw 日线必要字段，写出空表。")
        return pl.DataFrame(schema=schema)
    if not {"vt_symbol", "limit_up", "limit_down"}.issubset(instruments.columns):
        warnings.append("limit_status_daily: 缺少证券快照涨跌停字段，写出空表。")
        return pl.DataFrame(schema=schema)

    snapshot = instruments.select(
        "vt_symbol",
        pl.col("limit_up").cast(pl.Float64).alias("limit_up_snapshot"),
        pl.col("limit_down").cast(pl.Float64).alias("limit_down_snapshot"),
    )
    return (
        raw.select("datetime", "vt_symbol", pl.col("close").cast(pl.Float64))
        .join(snapshot, on="vt_symbol", how="left")
        .with_columns(
            (pl.col("close") >= pl.col("limit_up_snapshot")).fill_null(False).alias("is_limit_up"),
            (pl.col("close") <= pl.col("limit_down_snapshot")).fill_null(False).alias("is_limit_down"),
            pl.lit("instrument_master_snapshot").alias("source"),
            pl.lit("snapshot_based").alias("pit_status"),
        )
        .select(list(schema))
    )


def _build_suspension_daily(raw: pl.DataFrame, daily_status: pl.DataFrame, warnings: list[str]) -> pl.DataFrame:
    schema = {
        "datetime": pl.Date,
        "vt_symbol": pl.Utf8,
        "is_suspended": pl.Boolean,
        "reason": pl.Utf8,
        "source": pl.Utf8,
        "pit_status": pl.Utf8,
    }
    if {"datetime", "vt_symbol", "is_suspended"}.issubset(daily_status.columns):
        reason_expr = pl.col("status_reason").cast(pl.Utf8) if "status_reason" in daily_status.columns else pl.lit(None, dtype=pl.Utf8)
        return (
            daily_status.select(
                "datetime",
                "vt_symbol",
                pl.col("is_suspended").cast(pl.Boolean),
                reason_expr.alias("reason"),
                pl.lit("daily_status").alias("source"),
                pl.lit("derived_from_daily_status").alias("pit_status"),
            )
            .select(list(schema))
            .sort(["vt_symbol", "datetime"])
        )

    if not {"datetime", "vt_symbol", "volume", "turnover"}.issubset(raw.columns):
        warnings.append("suspension_daily: 缺少 daily_status，且 raw 日线字段不足，写出空表。")
        return pl.DataFrame(schema=schema)

    warnings.append("suspension_daily: 使用成交量/成交额为 0 的近似规则，后续应替换为官方停复牌历史。")
    return (
        raw.select(
            "datetime",
            "vt_symbol",
            ((pl.col("volume") <= 0) | (pl.col("turnover") <= 0)).fill_null(True).alias("is_suspended"),
        )
        .with_columns(
            pl.when(pl.col("is_suspended")).then(pl.lit("zero_volume_or_turnover")).otherwise(pl.lit(None, dtype=pl.Utf8)).alias("reason"),
            pl.lit("raw_bars_zero_volume_rule").alias("source"),
            pl.lit("approximation").alias("pit_status"),
        )
        .select(list(schema))
    )


def _build_st_status_daily(
    raw: pl.DataFrame,
    instruments: pl.DataFrame,
    daily_status: pl.DataFrame,
    warnings: list[str],
) -> pl.DataFrame:
    schema = {
        "datetime": pl.Date,
        "vt_symbol": pl.Utf8,
        "is_st": pl.Boolean,
        "name_snapshot": pl.Utf8,
        "source": pl.Utf8,
        "pit_status": pl.Utf8,
    }
    if {"datetime", "vt_symbol", "is_st"}.issubset(daily_status.columns):
        name_expr = pl.col("name").cast(pl.Utf8) if "name" in daily_status.columns else pl.lit(None, dtype=pl.Utf8)
        return (
            daily_status.select(
                "datetime",
                "vt_symbol",
                pl.col("is_st").cast(pl.Boolean),
                name_expr.alias("name_snapshot"),
                pl.lit("daily_status").alias("source"),
                pl.lit("derived_from_daily_status").alias("pit_status"),
            )
            .select(list(schema))
            .sort(["vt_symbol", "datetime"])
        )

    if not {"datetime", "vt_symbol"}.issubset(raw.columns) or not {"vt_symbol", "name"}.issubset(instruments.columns):
        warnings.append("st_status_daily: 缺少 daily_status 或证券名称快照，写出空表。")
        return pl.DataFrame(schema=schema)

    warnings.append("st_status_daily: 使用当前名称快照判断 ST，后续应替换为历史 ST 状态。")
    snapshot = instruments.select("vt_symbol", pl.col("name").cast(pl.Utf8).alias("name_snapshot"))
    return (
        raw.select("datetime", "vt_symbol")
        .join(snapshot, on="vt_symbol", how="left")
        .with_columns(
            pl.col("name_snapshot").str.contains("ST", literal=True).fill_null(False).alias("is_st"),
            pl.lit("instrument_master_snapshot").alias("source"),
            pl.lit("snapshot_based").alias("pit_status"),
        )
        .select(list(schema))
    )


def _read_parquet_or_empty(path: Path, warnings: list[str]) -> pl.DataFrame:
    if not path.exists():
        warnings.append(f"缺少输入文件: {path}")
        return pl.DataFrame()
    try:
        return pl.read_parquet(path)
    except Exception as exc:
        warnings.append(f"读取失败: {path}: {exc}")
        return pl.DataFrame()


def _first_existing_path(root: Path, candidates: list[str]) -> Path | None:
    for candidate in candidates:
        path = root / candidate
        if path.exists():
            return path
    return None


def _write_dataset(df: pl.DataFrame, path: Path, written: list[str], skipped: list[str]) -> None:
    if df.is_empty():
        skipped.append(str(path))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)
    written.append(str(path))


def _write_summary(result: PitTablesBuildResult) -> None:
    Path(result.summary_path).write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def _write_report(result: PitTablesBuildResult, root: Path) -> None:
    lines = [
        "# PIT 衍生表构建报告",
        "",
        f"- Created at: `{datetime.now().isoformat(timespec='seconds')}`",
        f"- Data root: `{root}`",
        f"- Written datasets: `{len(result.written)}`",
        f"- Skipped datasets: `{len(result.skipped)}`",
        "",
        "## 已写出",
        "",
    ]
    lines.extend([f"- `{path}`" for path in result.written] or ["- 无"])
    lines.extend(["", "## 跳过", ""])
    lines.extend([f"- `{path}`" for path in result.skipped] or ["- 无"])
    lines.extend(["", "## 风险提示", ""])
    lines.extend([f"- {warning}" for warning in result.warnings] or ["- 无"])
    lines.append("")
    Path(result.report_path).write_text("\n".join(lines), encoding="utf-8")
