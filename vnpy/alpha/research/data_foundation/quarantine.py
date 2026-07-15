from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import json
import shutil

import polars as pl


@dataclass(frozen=True)
class QuarantineResult:
    """Record of a failed source pull moved or described under data/quarantine."""

    source: str
    dataset: str
    reason: str
    quarantine_dir: str
    metadata_path: str
    copied_files: list[str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def quarantine_failed_source_pull(
    data_root: str | Path,
    *,
    source: str,
    dataset: str,
    reason: str,
    files: list[str | Path] | None = None,
) -> QuarantineResult:
    """Persist failed pull evidence outside raw/bronze/silver/gold promotion paths."""
    root = Path(data_root)
    safe_source = _safe_name(source)
    safe_dataset = _safe_name(dataset)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target_dir = root / "quarantine" / safe_source / safe_dataset / timestamp
    target_dir.mkdir(parents=True, exist_ok=False)

    copied_files: list[str] = []
    for file in files or []:
        source_path = Path(file)
        if not source_path.exists():
            continue
        target_path = target_dir / source_path.name
        if source_path.is_file():
            shutil.copy2(source_path, target_path)
            copied_files.append(str(target_path))

    result = QuarantineResult(
        source=source,
        dataset=dataset,
        reason=reason,
        quarantine_dir=str(target_dir),
        metadata_path=str(target_dir / "quarantine.json"),
        copied_files=copied_files,
    )
    Path(result.metadata_path).write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def collect_quarantined_symbols(data_root: str | Path) -> pl.DataFrame:
    """Collect symbol-level quarantines that should be excluded from execution universe."""
    root = Path(data_root)
    frames: list[pl.DataFrame] = []
    unknown_path = root / "quarantine" / "reconciliation_unknown_symbols.parquet"
    if unknown_path.exists():
        unknown = pl.read_parquet(unknown_path)
        if "vt_symbol" in unknown.columns and not unknown.is_empty():
            frames.append(
                unknown.select(
                    "vt_symbol",
                    pl.col("quarantine_reason").cast(pl.Utf8).alias("reason")
                    if "quarantine_reason" in unknown.columns
                    else pl.lit("qmt_akshare_unknown_price_diff").alias("reason"),
                )
            )

    if not frames:
        return pl.DataFrame(schema={"vt_symbol": pl.String, "reason": pl.String})
    return pl.concat(frames, how="vertical").unique(["vt_symbol", "reason"]).sort(["vt_symbol", "reason"])


def apply_symbol_quarantine_to_execution_universe(data_root: str | Path = "data") -> dict[str, object]:
    """Remove quarantined symbols from `gold/execution_universe.parquet` in-place."""
    root = Path(data_root)
    universe_path = root / "gold" / "execution_universe.parquet"
    report_path = root / "quality" / "quarantine_report.md"
    symbols_path = root / "quarantine" / "symbol_quarantine.parquet"
    if not universe_path.exists():
        raise FileNotFoundError(f"execution universe not found: {universe_path}")

    quarantined = collect_quarantined_symbols(root)
    symbols_path.parent.mkdir(parents=True, exist_ok=True)
    quarantined.write_parquet(symbols_path)

    before = pl.read_parquet(universe_path)
    before_rows = before.height
    before_symbols = before.get_column("vt_symbol").n_unique() if "vt_symbol" in before.columns and before.height else 0
    if quarantined.is_empty():
        after = before
    else:
        after = before.join(quarantined.select("vt_symbol").unique(), on="vt_symbol", how="anti")
        temp = universe_path.with_suffix(".tmp.parquet")
        after.write_parquet(temp)
        temp.rename(universe_path)

    after_rows = after.height
    after_symbols = after.get_column("vt_symbol").n_unique() if "vt_symbol" in after.columns and after.height else 0
    payload = {
        "symbol_quarantine_path": str(symbols_path),
        "execution_universe_path": str(universe_path),
        "quarantined_symbol_count": quarantined.get_column("vt_symbol").n_unique() if not quarantined.is_empty() else 0,
        "rows_before": before_rows,
        "rows_after": after_rows,
        "removed_rows": before_rows - after_rows,
        "symbols_before": before_symbols,
        "symbols_after": after_symbols,
        "removed_symbols": before_symbols - after_symbols,
    }
    _write_quarantine_report(payload, report_path)
    return payload


def _write_quarantine_report(payload: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Symbol Quarantine Report",
        "",
        f"- symbol_quarantine_path: `{payload['symbol_quarantine_path']}`",
        f"- execution_universe_path: `{payload['execution_universe_path']}`",
        f"- quarantined_symbol_count: `{payload['quarantined_symbol_count']}`",
        f"- rows_before: `{payload['rows_before']}`",
        f"- rows_after: `{payload['rows_after']}`",
        f"- removed_rows: `{payload['removed_rows']}`",
        f"- symbols_before: `{payload['symbols_before']}`",
        f"- symbols_after: `{payload['symbols_after']}`",
        f"- removed_symbols: `{payload['removed_symbols']}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in value.strip())
    return cleaned or "unknown"
