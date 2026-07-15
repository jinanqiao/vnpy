from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
import json

import polars as pl


@dataclass(frozen=True)
class CoreDatasetFreshness:
    """核心数据集的新鲜度摘要。"""

    dataset_name: str
    path: str
    date_column: str
    date_min: str | None
    date_max: str | None
    rows: int
    symbol_count_latest: int | None
    required_for_live: bool
    status: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CommonTradeDateResult:
    """核心数据共同交易日检查结果。"""

    status: str
    as_of: str
    common_trade_date: str | None
    datasets: list[CoreDatasetFreshness]
    blocking_datasets: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "as_of": self.as_of,
            "common_trade_date": self.common_trade_date,
            "datasets": [item.to_dict() for item in self.datasets],
            "blocking_datasets": self.blocking_datasets,
        }

    def write_json(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return output


CORE_DATASETS: tuple[tuple[str, str, str], ...] = (
    ("trading_calendar", "silver/trading_calendar.parquet", "trade_date"),
    ("daily_bars_raw_price", "silver/daily_bars_raw_price.parquet", "datetime"),
    ("daily_bars_adjusted", "silver/daily_bars_adjusted.parquet", "datetime"),
    ("execution_universe", "gold/execution_universe.parquet", "datetime"),
)


def evaluate_common_trade_date(data_root: str | Path = "data", *, as_of: date | str) -> CommonTradeDateResult:
    """检查 live 必需核心数据是否能形成同一交易日截面。"""
    root = Path(data_root)
    as_of_date = _to_date(as_of)
    datasets = [_inspect_dataset(root, name, relative_path, date_column) for name, relative_path, date_column in CORE_DATASETS]

    blocking = [item.dataset_name for item in datasets if item.status == "fail"]
    date_max_values = [_parse_optional_date(item.date_max) for item in datasets if item.status != "fail" and item.date_max]
    common = min(date_max_values) if date_max_values else None

    expected = _expected_trade_date(root, as_of_date)
    if common is None:
        blocking.extend([item.dataset_name for item in datasets if item.dataset_name not in blocking])
    elif _calendar_latest(datasets) and _calendar_latest(datasets) < as_of_date:
        blocking.append("trading_calendar")
    else:
        for item in datasets:
            item_date = _parse_optional_date(item.date_max)
            if item_date is None or (expected is not None and item.dataset_name != "trading_calendar" and item_date < expected):
                blocking.append(item.dataset_name)

    unique_blocking = sorted(set(blocking))
    return CommonTradeDateResult(
        status="fail" if unique_blocking else "pass",
        as_of=str(as_of_date),
        common_trade_date=str(common) if common else None,
        datasets=datasets,
        blocking_datasets=unique_blocking,
    )


def write_freshness_report(result: CommonTradeDateResult, path: str | Path) -> Path:
    """写出核心数据新鲜度 Markdown 报告。"""
    lines = [
        "# 核心数据新鲜度报告",
        "",
        f"- 状态: `{result.status}`",
        f"- 评估日期: `{result.as_of}`",
        f"- 共同交易日: `{result.common_trade_date}`",
        f"- 阻断数据集: `{', '.join(result.blocking_datasets) if result.blocking_datasets else '无'}`",
        "",
        "| 数据集 | 最新日期 | 行数 | 最新日股票数 | 状态 | 说明 |",
        "|---|---:|---:|---:|---|---|",
    ]
    for item in result.datasets:
        symbol_count = "-" if item.symbol_count_latest is None else str(item.symbol_count_latest)
        lines.append(
            f"| {item.dataset_name} | {item.date_max or '-'} | {item.rows} | "
            f"{symbol_count} | {item.status} | {item.detail} |"
        )
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def _inspect_dataset(root: Path, dataset_name: str, relative_path: str, date_column: str) -> CoreDatasetFreshness:
    path = root / relative_path
    if not path.exists():
        return CoreDatasetFreshness(dataset_name, relative_path, date_column, None, None, 0, None, True, "fail", "文件缺失")
    try:
        df = pl.read_parquet(path)
    except Exception as exc:
        return CoreDatasetFreshness(dataset_name, relative_path, date_column, None, None, 0, None, True, "fail", f"读取失败: {exc}")
    if date_column not in df.columns:
        return CoreDatasetFreshness(dataset_name, relative_path, date_column, None, None, df.height, None, True, "fail", f"缺少日期字段 {date_column}")
    if df.is_empty():
        return CoreDatasetFreshness(dataset_name, relative_path, date_column, None, None, 0, None, True, "fail", "空表")

    date_range = df.select(
        pl.col(date_column).cast(pl.Date).min().alias("date_min"),
        pl.col(date_column).cast(pl.Date).max().alias("date_max"),
    ).row(0, named=True)
    latest = date_range["date_max"]
    symbol_count = None
    if "vt_symbol" in df.columns and latest is not None:
        symbol_count = int(
            df.filter(pl.col(date_column).cast(pl.Date) == latest)
            .select(pl.col("vt_symbol").n_unique())
            .item()
        )
    return CoreDatasetFreshness(
        dataset_name=dataset_name,
        path=relative_path,
        date_column=date_column,
        date_min=str(date_range["date_min"]) if date_range["date_min"] else None,
        date_max=str(latest) if latest else None,
        rows=df.height,
        symbol_count_latest=symbol_count,
        required_for_live=True,
        status="pass",
        detail="可读取",
    )


def _calendar_latest(datasets: list[CoreDatasetFreshness]) -> date | None:
    for item in datasets:
        if item.dataset_name == "trading_calendar":
            return _parse_optional_date(item.date_max)
    return None


def _expected_trade_date(root: Path, as_of: date) -> date | None:
    path = root / "silver" / "trading_calendar.parquet"
    if not path.exists():
        return None
    try:
        calendar = pl.read_parquet(path)
    except Exception:
        return None
    if "trade_date" not in calendar.columns:
        return None
    dates = (
        calendar.with_columns(pl.col("trade_date").cast(pl.Date))
        .filter(pl.col("trade_date") <= as_of)
        .select("trade_date")
        .unique()
        .sort("trade_date")
        .get_column("trade_date")
        .to_list()
    )
    return dates[-1] if dates else None


def _parse_optional_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value.split(" ")[0])


def _to_date(value: date | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)
