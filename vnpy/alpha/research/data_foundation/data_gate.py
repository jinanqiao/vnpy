from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any
import json

import polars as pl

from .price_reconciliation import diagnose_price_reconciliation


class DataGateMode(str, Enum):
    """Data readiness mode."""

    RESEARCH = "research"
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True)
class DataGateCheck:
    """One data gate check result."""

    name: str
    dataset: str
    severity: str
    status: str
    detail: str
    observed_value: Any = None
    expected_value: Any = None


@dataclass(frozen=True)
class DataGateResult:
    """Aggregated data gate result."""

    status: str
    mode: str
    latest_trade_date: str | None
    expected_trade_date: str | None
    blocking_checks: list[DataGateCheck] = field(default_factory=list)
    warning_checks: list[DataGateCheck] = field(default_factory=list)
    passed_checks: list[DataGateCheck] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "mode": self.mode,
            "latest_trade_date": self.latest_trade_date,
            "expected_trade_date": self.expected_trade_date,
            "blocking_checks": [asdict(item) for item in self.blocking_checks],
            "warning_checks": [asdict(item) for item in self.warning_checks],
            "passed_checks": [asdict(item) for item in self.passed_checks],
            "summary": {
                "blocking_failures": len(self.blocking_checks),
                "warnings": len(self.warning_checks),
                "passed": len(self.passed_checks),
            },
        }

    def write_json(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return output


REQUIRED_DATASETS: dict[str, str] = {
    "daily_bars_raw_price": "silver/daily_bars_raw_price.parquet",
    "daily_bars_adjusted": "silver/daily_bars_adjusted.parquet",
    "trading_calendar": "silver/trading_calendar.parquet",
    "execution_universe": "gold/execution_universe.parquet",
}

AUXILIARY_DATASETS: dict[str, str] = {
    "benchmark_index_daily": "silver/benchmark_index_daily.parquet",
    "instrument_master_snapshot": "silver/instrument_master_snapshot.parquet",
    "sector_members_snapshot": "silver/sector_members_snapshot.parquet",
    "sw1_members_snapshot": "silver/sw1_members_snapshot.parquet",
    "financial_reports_pit": "silver/financial_reports_pit.parquet",
}


def run_data_gate(
    data_root: str | Path = "data",
    *,
    mode: DataGateMode | str = DataGateMode.RESEARCH,
    as_of: date | str | None = None,
    source_coverage_min: float = 0.99,
    source_price_diff_max: float = 0.02,
    source_recent_days: int = 252,
    snapshot_max_age_days: int = 7,
) -> DataGateResult:
    """Run a local data readiness gate over the layered data foundation."""
    root = Path(data_root)
    mode = DataGateMode(mode)
    as_of_date = _to_date(as_of) if as_of else date.today()

    checks: list[DataGateCheck] = []
    frames: dict[str, pl.DataFrame] = {}

    for name, relative_path in REQUIRED_DATASETS.items():
        path = root / relative_path
        if not path.exists():
            checks.append(_check(name="required_file_exists", dataset=name, severity="blocking", status="fail", detail=f"缺少必需文件: {relative_path}"))
            continue
        checks.append(_check(name="required_file_exists", dataset=name, severity="blocking", status="pass", detail=f"文件存在: {relative_path}"))
        frames[name] = pl.read_parquet(path)

    for name, relative_path in AUXILIARY_DATASETS.items():
        path = root / relative_path
        if not path.exists():
            checks.append(_check(
                name="auxiliary_file_exists",
                dataset=name,
                severity=_severity_for_mode(mode, research_warning=True),
                status="fail",
                detail=f"缺少辅助数据文件: {relative_path}",
            ))
            continue
        checks.append(_check(
            name="auxiliary_file_exists",
            dataset=name,
            severity=_severity_for_mode(mode, research_warning=True),
            status="pass",
            detail=f"辅助数据文件存在: {relative_path}",
        ))
        frames[name] = pl.read_parquet(path)

    if "daily_bars_raw_price" in frames:
        checks.extend(_check_price_frame("daily_bars_raw_price", frames["daily_bars_raw_price"]))
    if "daily_bars_adjusted" in frames:
        checks.extend(_check_price_frame("daily_bars_adjusted", frames["daily_bars_adjusted"]))
    if "daily_bars_raw_price" in frames and "daily_bars_adjusted" in frames:
        checks.extend(_check_adjusted_consistency(frames["daily_bars_raw_price"], frames["daily_bars_adjusted"], mode))
    if "daily_bars_adjusted" in frames:
        checks.extend(
            _check_akshare_price_comparison(
                root,
                frames["daily_bars_adjusted"],
                mode,
                coverage_min=source_coverage_min,
                price_diff_max=source_price_diff_max,
                recent_days=source_recent_days,
            )
        )
    calendar_covers_as_of = False
    if "trading_calendar" in frames:
        calendar_check = _calendar_coverage_check(frames["trading_calendar"], as_of_date, mode)
        checks.append(calendar_check)
        calendar_covers_as_of = calendar_check.status == "pass"

    if calendar_covers_as_of and "daily_bars_raw_price" in frames:
        expected = _expected_trade_date(frames["trading_calendar"], as_of_date)
        latest = _max_date(frames["daily_bars_raw_price"], "datetime")
        checks.append(_freshness_check(latest, expected, mode))
    else:
        expected = None
        latest = _max_date(frames["daily_bars_raw_price"], "datetime") if "daily_bars_raw_price" in frames else None

    if "execution_universe" in frames and expected:
        universe_latest = _max_date(frames["execution_universe"], "datetime")
        status = "pass" if universe_latest and universe_latest >= expected else "fail"
        checks.append(_check(
            name="execution_universe_latest_date",
            dataset="execution_universe",
            severity=_severity_for_mode(mode, research_warning=True),
            status=status,
            detail="可执行股票池覆盖最新允许交易日" if status == "pass" else "可执行股票池未覆盖最新允许交易日",
            observed_value=str(universe_latest) if universe_latest else None,
            expected_value=str(expected),
        ))

    if expected:
        if "benchmark_index_daily" in frames:
            checks.append(_dataset_latest_date_check(
                "benchmark_index_daily",
                frames["benchmark_index_daily"],
                "datetime",
                expected,
                mode,
                "基准指数日线覆盖最新允许交易日",
                "基准指数日线未覆盖最新允许交易日",
            ))
        for dataset in ["instrument_master_snapshot", "sector_members_snapshot", "sw1_members_snapshot"]:
            if dataset in frames:
                checks.append(_snapshot_age_check(dataset, frames[dataset], max(as_of_date, date.today()), mode, snapshot_max_age_days))
        if "financial_reports_pit" in frames:
            checks.extend(_financial_report_checks(frames["financial_reports_pit"], mode))

    blocking = [item for item in checks if item.status == "fail" and item.severity == "blocking"]
    warnings = [item for item in checks if item.status == "fail" and item.severity == "warning"]
    passed = [item for item in checks if item.status == "pass"]
    status = "fail" if blocking else "warning" if warnings else "pass"
    return DataGateResult(
        status=status,
        mode=mode.value,
        latest_trade_date=str(latest) if latest else None,
        expected_trade_date=str(expected) if expected else None,
        blocking_checks=blocking,
        warning_checks=warnings,
        passed_checks=passed,
    )


def write_data_gate_report(result: DataGateResult, path: str | Path) -> Path:
    """Write a compact markdown data gate report."""
    lines = [
        "# 数据门禁报告",
        "",
        f"- 状态: `{result.status}`",
        f"- 模式: `{result.mode}`",
        f"- 最新行情交易日: `{result.latest_trade_date}`",
        f"- 预期交易日: `{result.expected_trade_date}`",
        f"- 阻断失败: `{len(result.blocking_checks)}`",
        f"- 警告: `{len(result.warning_checks)}`",
        "",
        "## 阻断项",
        "",
    ]
    if result.blocking_checks:
        lines.extend([f"- `{item.dataset}` / `{item.name}`: {item.detail}" for item in result.blocking_checks])
    else:
        lines.append("- 无")
    lines.extend(["", "## 警告项", ""])
    if result.warning_checks:
        lines.extend([f"- `{item.dataset}` / `{item.name}`: {item.detail}" for item in result.warning_checks])
    else:
        lines.append("- 无")

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def _check(
    *,
    name: str,
    dataset: str,
    severity: str,
    status: str,
    detail: str,
    observed_value: Any = None,
    expected_value: Any = None,
) -> DataGateCheck:
    return DataGateCheck(name, dataset, severity, status, detail, observed_value, expected_value)


def _check_price_frame(dataset: str, df: pl.DataFrame) -> list[DataGateCheck]:
    checks: list[DataGateCheck] = []
    required = {"datetime", "vt_symbol", "open", "high", "low", "close"}
    missing = sorted(required - set(df.columns))
    checks.append(_check(
        name="required_columns",
        dataset=dataset,
        severity="blocking",
        status="fail" if missing else "pass",
        detail=f"缺少字段: {missing}" if missing else "必需字段齐全",
        observed_value=missing,
        expected_value=sorted(required),
    ))
    if missing:
        return checks

    duplicate_count = df.group_by(["vt_symbol", "datetime"]).len().filter(pl.col("len") > 1).height
    checks.append(_check(
        name="duplicate_symbol_datetime",
        dataset=dataset,
        severity="blocking",
        status="fail" if duplicate_count else "pass",
        detail="存在重复股票日期行" if duplicate_count else "无重复股票日期行",
        observed_value=duplicate_count,
        expected_value=0,
    ))

    relation_errors = df.filter(
        (pl.col("high") < pl.col("low"))
        | (pl.col("open") > pl.col("high"))
        | (pl.col("open") < pl.col("low"))
        | (pl.col("close") > pl.col("high"))
        | (pl.col("close") < pl.col("low"))
    ).height
    checks.append(_check(
        name="ohlc_relation",
        dataset=dataset,
        severity="blocking",
        status="fail" if relation_errors else "pass",
        detail="存在 OHLC 关系错误" if relation_errors else "OHLC 关系正常",
        observed_value=relation_errors,
        expected_value=0,
    ))

    non_positive_rows = df.filter(
        (pl.col("open") <= 0)
        | (pl.col("high") <= 0)
        | (pl.col("low") <= 0)
        | (pl.col("close") <= 0)
    ).height
    checks.append(_check(
        name="positive_ohlc",
        dataset=dataset,
        severity="blocking",
        status="fail" if non_positive_rows else "pass",
        detail="存在非正 OHLC 价格" if non_positive_rows else "OHLC 价格均为正",
        observed_value=non_positive_rows,
        expected_value=0,
    ))

    null_counts = df.select([pl.col(column).is_null().sum().alias(column) for column in required]).row(0, named=True)
    null_total = int(sum(null_counts.values()))
    checks.append(_check(
        name="required_nulls",
        dataset=dataset,
        severity="blocking",
        status="fail" if null_total else "pass",
        detail="必需字段存在空值" if null_total else "必需字段无空值",
        observed_value=null_counts,
        expected_value=0,
    ))
    return checks


def _check_adjusted_consistency(raw: pl.DataFrame, adjusted: pl.DataFrame, mode: DataGateMode) -> list[DataGateCheck]:
    required = {"datetime", "vt_symbol", "turnover"}
    if not required.issubset(raw.columns) or not required.issubset(adjusted.columns):
        return [_check(
            name="adjusted_turnover_consistency",
            dataset="daily_bars_adjusted",
            severity=_severity_for_mode(mode, research_warning=True),
            status="fail",
            detail="原始或后复权数据缺少 turnover，无法校验成交额一致性",
        )]

    raw_keys = raw.select(["vt_symbol", "datetime"]).unique()
    adjusted_keys = adjusted.select(["vt_symbol", "datetime"]).unique()
    joined_keys = adjusted_keys.join(raw_keys, on=["vt_symbol", "datetime"], how="inner")
    coverage = joined_keys.height / max(raw_keys.height, 1)

    joined = adjusted.join(raw, on=["vt_symbol", "datetime"], how="inner", suffix="_raw")
    mismatch_rows = joined.filter(
        ((pl.col("turnover") - pl.col("turnover_raw")).abs() / pl.col("turnover_raw").abs().clip(lower_bound=1.0)) > 0.001
    ).height
    severity = _severity_for_mode(mode, research_warning=True)
    return [
        _check(
            name="adjusted_key_coverage",
            dataset="daily_bars_adjusted",
            severity=severity,
            status="fail" if coverage < 0.99 else "pass",
            detail="后复权覆盖率低于 99%" if coverage < 0.99 else "后复权覆盖率达标",
            observed_value=round(coverage, 6),
            expected_value=">=0.99",
        ),
        _check(
            name="adjusted_turnover_consistency",
            dataset="daily_bars_adjusted",
            severity=severity,
            status="fail" if mismatch_rows else "pass",
            detail="后复权与未复权成交额存在差异" if mismatch_rows else "后复权与未复权成交额一致",
            observed_value=mismatch_rows,
            expected_value=0,
        ),
    ]


def _unknown_symbols_in_execution(root: Path, unknown_symbols_path: str | None) -> dict[str, Any]:
    if not unknown_symbols_path:
        return {"overlap_count": 0, "sample": []}
    unknown_path = Path(unknown_symbols_path)
    execution_path = root / "gold" / "execution_universe.parquet"
    if not unknown_path.exists() or not execution_path.exists():
        return {"overlap_count": 0, "sample": []}
    unknown = pl.read_parquet(unknown_path)
    if unknown.is_empty() or "vt_symbol" not in unknown.columns:
        return {"overlap_count": 0, "sample": []}
    execution = pl.read_parquet(execution_path).select("vt_symbol").unique()
    overlap = unknown.select("vt_symbol").unique().join(execution, on="vt_symbol", how="inner")
    return {
        "overlap_count": overlap.height,
        "sample": overlap.head(20).get_column("vt_symbol").to_list() if overlap.height else [],
    }


def _check_akshare_price_comparison(
    root: Path,
    adjusted: pl.DataFrame,
    mode: DataGateMode,
    *,
    coverage_min: float,
    price_diff_max: float,
    recent_days: int,
) -> list[DataGateCheck]:
    severity = _severity_for_mode(mode, research_warning=True)
    result = diagnose_price_reconciliation(
        root,
        recent_days=recent_days,
        price_diff_max=price_diff_max,
        coverage_min=coverage_min,
        max_samples=5,
    )
    if "missing_input" in result.suspected_reasons:
        comparison_path = root / "silver" / "outstanding_share_turnover.parquet"
        return [
            _check(
                name="qmt_akshare_price_comparison_available",
                dataset="outstanding_share_turnover",
                severity=severity,
                status="fail",
                detail="缺少 AKShare 后复权价格对照表，无法做 QMT/AKShare 价格交叉校验",
                observed_value=None,
                expected_value=str(comparison_path.relative_to(root)),
            )
        ]
    if "missing_columns" in result.suspected_reasons:
        return [
            _check(
                name="qmt_akshare_price_comparison_columns",
                dataset="outstanding_share_turnover",
                severity=severity,
                status="fail",
                detail="QMT/AKShare 价格交叉校验缺少必要字段",
                observed_value=None,
                expected_value="daily_bars_adjusted(datetime, vt_symbol, close) + outstanding_share_turnover(datetime, vt_symbol, close_hfq)",
            )
        ]

    unknown_overlap = _unknown_symbols_in_execution(root, result.unknown_symbols_path)
    return [
        _check(
            name="qmt_akshare_key_coverage",
            dataset="outstanding_share_turnover",
            severity=severity,
            status="fail" if result.coverage < coverage_min else "pass",
            detail="AKShare 对照价格覆盖率低于阈值" if result.coverage < coverage_min else "AKShare 对照价格覆盖率达标",
            observed_value=result.coverage,
            expected_value=f">={coverage_min}",
        ),
        _check(
            name="qmt_akshare_price_diff",
            dataset="outstanding_share_turnover",
            severity=severity,
            status="fail" if result.normalized_diff_rows else "pass",
            detail=(
                "QMT/AKShare 后复权价格按股票尺度归一化后仍超过阈值"
                if result.normalized_diff_rows
                else "QMT/AKShare 后复权价格归一化差异在阈值内"
            ),
            observed_value={
                "absolute_diff_rows": result.diff_rows,
                "absolute_diff_ratio": result.diff_ratio,
                "normalized_diff_rows": result.normalized_diff_rows,
                "normalized_diff_ratio": result.normalized_diff_ratio,
            },
            expected_value=f"normalized_relative_diff<={price_diff_max}",
        ),
        _check(
            name="qmt_akshare_unknown_quarantine",
            dataset="price_reconciliation",
            severity=severity,
            status="fail" if unknown_overlap["overlap_count"] else "pass",
            detail=(
                "未分类 QMT/AKShare 价差异常股票仍在执行股票池中"
                if unknown_overlap["overlap_count"]
                else "未分类 QMT/AKShare 价差异常股票未进入执行股票池"
            ),
            observed_value={
                "unknown_symbol_count": result.unknown_symbol_count,
                "unknown_in_execution_count": unknown_overlap["overlap_count"],
                "unknown_symbols_path": result.unknown_symbols_path,
                "sample": unknown_overlap["sample"],
            },
            expected_value=0,
        ),
    ]


def _freshness_check(latest: date | None, expected: date | None, mode: DataGateMode) -> DataGateCheck:
    ok = bool(latest and expected and latest >= expected)
    return _check(
        name="latest_trade_date_freshness",
        dataset="daily_bars_raw_price",
        severity=_severity_for_mode(mode, research_warning=True),
        status="pass" if ok else "fail",
        detail="行情新鲜度达标" if ok else "行情日期早于预期交易日",
        observed_value=str(latest) if latest else None,
        expected_value=str(expected) if expected else None,
    )


def _dataset_latest_date_check(
    dataset: str,
    df: pl.DataFrame,
    column: str,
    expected: date,
    mode: DataGateMode,
    pass_detail: str,
    fail_detail: str,
) -> DataGateCheck:
    latest = _max_date(df, column)
    ok = bool(latest and latest >= expected)
    return _check(
        name="latest_trade_date_freshness",
        dataset=dataset,
        severity=_severity_for_mode(mode, research_warning=True),
        status="pass" if ok else "fail",
        detail=pass_detail if ok else fail_detail,
        observed_value=str(latest) if latest else None,
        expected_value=str(expected),
    )


def _snapshot_age_check(
    dataset: str,
    df: pl.DataFrame,
    as_of: date,
    mode: DataGateMode,
    max_age_days: int,
) -> DataGateCheck:
    if "snapshot_at" not in df.columns or df.is_empty():
        return _check(
            name="snapshot_age",
            dataset=dataset,
            severity=_severity_for_mode(mode, research_warning=True),
            status="fail",
            detail="快照数据缺少 snapshot_at，无法判断新鲜度",
            observed_value=None,
            expected_value=f"<={max_age_days} days",
        )
    latest = _max_date(df, "snapshot_at")
    age_days = (as_of - latest).days if latest else None
    ok = age_days is not None and 0 <= age_days <= max_age_days
    return _check(
        name="snapshot_age",
        dataset=dataset,
        severity=_severity_for_mode(mode, research_warning=True),
        status="pass" if ok else "fail",
        detail="快照新鲜度达标" if ok else "快照过旧或日期异常",
        observed_value={"latest_snapshot_date": str(latest) if latest else None, "age_days": age_days},
        expected_value=f"0..{max_age_days} days",
    )


def _financial_report_checks(df: pl.DataFrame, mode: DataGateMode) -> list[DataGateCheck]:
    checks: list[DataGateCheck] = []
    required = {"vt_symbol", "report_date", "announce_date"}
    missing = sorted(required - set(df.columns))
    checks.append(_check(
        name="required_columns",
        dataset="financial_reports_pit",
        severity=_severity_for_mode(mode, research_warning=True),
        status="fail" if missing else "pass",
        detail=f"财报 PIT 表缺少字段: {missing}" if missing else "财报 PIT 必需字段齐全",
        observed_value=missing,
        expected_value=sorted(required),
    ))
    if missing:
        return checks
    total_rows = max(df.height, 1)
    announce_nulls = df.filter(pl.col("announce_date").is_null()).height
    null_ratio = announce_nulls / total_rows
    checks.append(_check(
        name="announce_date_coverage",
        dataset="financial_reports_pit",
        severity=_severity_for_mode(mode, research_warning=True),
        status="fail" if null_ratio > 0.01 else "pass",
        detail="财报公告日缺失率过高" if null_ratio > 0.01 else "财报公告日覆盖率达标",
        observed_value=round(null_ratio, 6),
        expected_value="<=0.01",
    ))
    return checks


def _calendar_coverage_check(calendar: pl.DataFrame, as_of: date, mode: DataGateMode) -> DataGateCheck:
    latest_calendar_date = _max_date(calendar, "trade_date")
    ok = bool(latest_calendar_date and latest_calendar_date >= as_of)
    return _check(
        name="trading_calendar_coverage",
        dataset="trading_calendar",
        severity=_severity_for_mode(mode, research_warning=True),
        status="pass" if ok else "fail",
        detail="交易日历覆盖评估日期" if ok else "交易日历未覆盖评估日期，无法可靠判断最新交易日",
        observed_value=str(latest_calendar_date) if latest_calendar_date else None,
        expected_value=f">={as_of}",
    )


def _severity_for_mode(mode: DataGateMode, *, research_warning: bool = False) -> str:
    if research_warning and mode == DataGateMode.RESEARCH:
        return "warning"
    return "blocking" if mode in {DataGateMode.BACKTEST, DataGateMode.PAPER, DataGateMode.LIVE} else "warning"


def _expected_trade_date(calendar: pl.DataFrame, as_of: date) -> date | None:
    if "trade_date" not in calendar.columns:
        return None
    dates = (
        calendar
        .with_columns(pl.col("trade_date").cast(pl.Date))
        .filter(pl.col("trade_date") <= as_of)
        .get_column("trade_date")
        .unique()
        .sort()
        .to_list()
    )
    return dates[-1] if dates else None


def _max_date(df: pl.DataFrame, column: str) -> date | None:
    if column not in df.columns or df.is_empty():
        return None
    value = df.select(pl.col(column).cast(pl.Date).max()).item()
    if isinstance(value, datetime):
        return value.date()
    return value


def _to_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)
