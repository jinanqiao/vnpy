from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
import hashlib
import json

import polars as pl


@dataclass(frozen=True)
class DatasetManifestItem:
    """Manifest entry for one local dataset file."""

    name: str
    layer: str
    source: str
    path: str
    sha256: str
    bytes: int
    rows: int | None
    columns: list[str]
    date_min: str | None
    date_max: str | None
    symbol_count: int | None
    pit_status: str


@dataclass(frozen=True)
class DataFoundationManifest:
    """Immutable-ish manifest snapshot for the local data foundation."""

    version_id: str
    created_at: str
    data_root: str
    latest_trade_date: str | None
    datasets: list[DatasetManifestItem]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version_id": self.version_id,
            "created_at": self.created_at,
            "data_root": self.data_root,
            "latest_trade_date": self.latest_trade_date,
            "datasets": [asdict(item) for item in self.datasets],
            "notes": self.notes,
        }

    def write_json(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return output

    def write_version_json(self, path: str | Path) -> Path:
        """Write the immutable version copy; refuse to overwrite an existing version."""
        output = Path(path)
        if output.exists():
            raise FileExistsError(f"manifest 版本文件已存在，拒绝覆盖: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return output


def build_manifest(
    data_root: str | Path,
    dataset_specs: list[dict[str, str]],
    *,
    version_label: str = "data-foundation",
) -> DataFoundationManifest:
    """Build a manifest from the layered dataset files that exist."""
    root = Path(data_root)
    created_at = datetime.now().isoformat(timespec="seconds")
    version_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{version_label}"
    items: list[DatasetManifestItem] = []

    for spec in dataset_specs:
        path = root / spec["target"]
        if not path.exists():
            continue
        items.append(_manifest_item(root, path, spec))

    latest_trade_date = _latest_trade_date(items)
    notes = [
        "大规模行情数据保留为 parquet；manifest 记录当前分层文件的内容哈希。",
        "本 manifest 是数据底座构建快照，不修改 QMT 凭证配置。",
    ]
    return DataFoundationManifest(
        version_id=version_id,
        created_at=created_at,
        data_root=str(root),
        latest_trade_date=latest_trade_date,
        datasets=items,
        notes=notes,
    )


def write_manifest_files(manifest: DataFoundationManifest, manifest_dir: str | Path) -> tuple[Path, Path]:
    """Write current manifest pointer and immutable version copy."""
    root = Path(manifest_dir)
    version_path = manifest.write_version_json(root / "versions" / f"{manifest.version_id}.json")
    current_path = manifest.write_json(root / "data_foundation_manifest.json")
    return current_path, version_path


def write_manifest_report(manifest: DataFoundationManifest, path: str | Path) -> Path:
    """Write a compact human-readable manifest report."""
    lines = [
        "# 数据底座 Manifest 报告",
        "",
        f"- Version: `{manifest.version_id}`",
        f"- Created at: `{manifest.created_at}`",
        f"- Data root: `{manifest.data_root}`",
        f"- Latest trade date: `{manifest.latest_trade_date}`",
        f"- Dataset count: `{len(manifest.datasets)}`",
        "",
        "| 数据集 | 层级 | 行数 | 日期范围 | 路径 |",
        "|---|---|---:|---|---|",
    ]
    for item in manifest.datasets:
        date_range = "-"
        if item.date_min or item.date_max:
            date_range = f"{item.date_min or '?'} ~ {item.date_max or '?'}"
        rows = "-" if item.rows is None else str(item.rows)
        lines.append(f"| {item.name} | {item.layer} | {rows} | {date_range} | `{item.path}` |")

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def _manifest_item(root: Path, path: Path, spec: dict[str, str]) -> DatasetManifestItem:
    columns: list[str] = []
    rows: int | None = None
    date_min: str | None = None
    date_max: str | None = None
    symbol_count: int | None = None

    if path.suffix == ".parquet":
        try:
            df = pl.read_parquet(path)
            rows = df.height
            columns = df.columns
            date_column = _first_existing(columns, ["datetime", "trade_date", "date"])
            if date_column:
                date_range = df.select(
                    pl.col(date_column).min().alias("min"),
                    pl.col(date_column).max().alias("max"),
                ).row(0, named=True)
                date_min = str(date_range["min"]) if date_range["min"] is not None else None
                date_max = str(date_range["max"]) if date_range["max"] is not None else None
            if "vt_symbol" in columns:
                symbol_count = int(df.select(pl.col("vt_symbol").n_unique()).item())
        except Exception:
            columns = []

    return DatasetManifestItem(
        name=spec["name"],
        layer=spec["layer"],
        source=spec["source"],
        path=str(path.relative_to(root)),
        sha256=_sha256(path),
        bytes=path.stat().st_size,
        rows=rows,
        columns=columns,
        date_min=date_min,
        date_max=date_max,
        symbol_count=symbol_count,
        pit_status=spec.get("pit_status", "snapshot_based"),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _first_existing(columns: list[str], candidates: list[str]) -> str | None:
    for column in candidates:
        if column in columns:
            return column
    return None


def _latest_trade_date(items: list[DatasetManifestItem]) -> str | None:
    """Return the coherent latest date shared by core trading datasets."""
    candidates = [
        item.date_max.split(" ")[0]
        for item in items
        if item.name in {"daily_bars_raw_price", "daily_bars_adjusted", "execution_universe", "trading_calendar"}
        and item.date_max
    ]
    return min(candidates) if candidates else None
