from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import shutil

from .manifest import DataFoundationManifest, build_manifest, write_manifest_files, write_manifest_report
from .metadata import initialize_metadata_store
from .query_catalog import write_query_catalog_sql


@dataclass(frozen=True)
class DatasetMapping:
    """Mapping from the current local data layout to the layered layout."""

    name: str
    source: str
    target: str
    layer: str
    provider: str
    pit_status: str = "snapshot_based"


@dataclass(frozen=True)
class DataFoundationBuildResult:
    """Result of creating the layered local data layout."""

    data_root: str
    copied: list[str]
    skipped_existing: list[str]
    missing_sources: list[str]
    manifest_path: str
    manifest_version_path: str
    report_path: str
    metadata_path: str
    query_catalog_path: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


DEFAULT_DATASET_MAPPINGS: tuple[DatasetMapping, ...] = (
    DatasetMapping("daily_bars_raw_price", "normalized/daily_bars_all_a.parquet", "silver/daily_bars_raw_price.parquet", "silver", "qmt", "snapshot_based"),
    DatasetMapping("daily_bars_adjusted", "normalized/daily_bars_all_a_adjusted.parquet", "silver/daily_bars_adjusted.parquet", "silver", "qmt", "snapshot_based"),
    DatasetMapping("benchmark_index_daily", "benchmark/index_daily.parquet", "silver/benchmark_index_daily.parquet", "silver", "qmt", "snapshot_based"),
    DatasetMapping("trading_calendar", "calendar/trading_dates.parquet", "silver/trading_calendar.parquet", "silver", "qmt", "pit_safe"),
    DatasetMapping("instrument_master_snapshot", "universe/all_a_symbols.parquet", "silver/instrument_master_snapshot.parquet", "silver", "qmt", "snapshot_based"),
    DatasetMapping("sector_members_snapshot", "sector/sector_members.parquet", "silver/sector_members_snapshot.parquet", "silver", "qmt", "snapshot_based"),
    DatasetMapping("sw1_members_snapshot", "sector/sw1_members.parquet", "silver/sw1_members_snapshot.parquet", "silver", "qmt", "snapshot_based"),
    DatasetMapping("outstanding_share_turnover", "akshare/daily_bars_outstanding.parquet", "silver/outstanding_share_turnover.parquet", "silver", "akshare", "snapshot_based"),
    DatasetMapping("financial_reports_pit", "akshare/financial_reports.parquet", "silver/financial_reports_pit.parquet", "silver", "akshare", "approximation"),
    DatasetMapping("financial_indicators", "akshare/financial_indicators.parquet", "silver/financial_indicators.parquet", "silver", "akshare", "approximation"),
    DatasetMapping("execution_universe", "universe/execution_universe.parquet", "gold/execution_universe.parquet", "gold", "qmt", "snapshot_based"),
    DatasetMapping("research_universe", "universe/research_universe.parquet", "gold/research_universe.parquet", "gold", "qmt", "snapshot_based"),
)

GENERATED_DATASET_MAPPINGS: tuple[DatasetMapping, ...] = (
    DatasetMapping("adjust_factors", "silver/adjust_factors.parquet", "silver/adjust_factors.parquet", "silver", "derived", "derived_from_adjusted_bars"),
    DatasetMapping("corporate_actions_candidates", "silver/corporate_actions_candidates.parquet", "silver/corporate_actions_candidates.parquet", "silver", "derived", "candidate_from_factor_change"),
    DatasetMapping("limit_status_daily", "silver/limit_status_daily.parquet", "silver/limit_status_daily.parquet", "silver", "derived", "snapshot_based"),
    DatasetMapping("suspension_daily", "silver/suspension_daily.parquet", "silver/suspension_daily.parquet", "silver", "derived", "derived_or_approximation"),
    DatasetMapping("st_status_daily", "silver/st_status_daily.parquet", "silver/st_status_daily.parquet", "silver", "derived", "derived_or_snapshot_based"),
)


def build_data_foundation_layout(
    data_root: str | Path = "data",
    *,
    overwrite: bool = False,
    write_summary: bool = True,
    state_dir: str | Path = "state",
) -> DataFoundationBuildResult:
    """Create the additive layered data layout without touching legacy files."""
    root = Path(data_root)
    _ensure_directories(root)

    copied: list[str] = []
    skipped_existing: list[str] = []
    missing_sources: list[str] = []

    for mapping in DEFAULT_DATASET_MAPPINGS:
        source = root / mapping.source
        target = root / mapping.target
        if target.exists() and not overwrite:
            skipped_existing.append(mapping.target)
            continue
        if target.exists() and not source.exists():
            skipped_existing.append(mapping.target)
            continue
        if not source.exists():
            missing_sources.append(mapping.source)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(mapping.target)

    manifest = _build_default_manifest(root)
    manifest_path, manifest_version_path = write_manifest_files(manifest, root / "manifest")
    report_path = write_manifest_report(manifest, root / "quality" / "data_foundation_manifest.md")
    metadata_path = initialize_metadata_store(manifest, state_dir=state_dir)
    query_catalog_path = write_query_catalog_sql(Path(state_dir) / "query_catalog.sql")

    result = DataFoundationBuildResult(
        data_root=str(root),
        copied=copied,
        skipped_existing=skipped_existing,
        missing_sources=missing_sources,
        manifest_path=str(manifest_path),
        manifest_version_path=str(manifest_version_path),
        report_path=str(report_path),
        metadata_path=str(metadata_path),
        query_catalog_path=str(query_catalog_path),
    )

    if write_summary:
        summary_path = root / "quality" / "data_foundation_build_summary.json"
        summary_path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def mapping_specs() -> list[dict[str, str]]:
    """Return manifest-friendly mapping specs."""
    return [
        {
            "name": mapping.name,
            "source": mapping.provider,
            "target": mapping.target,
            "layer": mapping.layer,
            "pit_status": mapping.pit_status,
        }
        for mapping in [*DEFAULT_DATASET_MAPPINGS, *GENERATED_DATASET_MAPPINGS]
    ]


def _ensure_directories(root: Path) -> None:
    for folder in [
        "raw/qmt",
        "raw/akshare",
        "bronze",
        "silver",
        "gold",
        "manifest",
        "quality",
        "dictionary",
    ]:
        (root / folder).mkdir(parents=True, exist_ok=True)
    Path("state").mkdir(parents=True, exist_ok=True)


def _build_default_manifest(root: Path) -> DataFoundationManifest:
    return build_manifest(root, mapping_specs(), version_label="data-foundation")
