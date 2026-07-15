from __future__ import annotations

from pathlib import Path
import json
import sqlite3
from datetime import datetime

from .manifest import DataFoundationManifest


def initialize_metadata_store(
    manifest: DataFoundationManifest,
    *,
    state_dir: str | Path = "state",
) -> Path:
    """Create/update the local SQLite metadata store for data lineage."""
    state_path = Path(state_dir)
    state_path.mkdir(parents=True, exist_ok=True)
    db_path = state_path / "quant_meta.sqlite"

    with sqlite3.connect(db_path) as conn:
        _create_schema(conn)
        _upsert_manifest(conn, manifest)
        _upsert_datasets(conn, manifest)
        _upsert_query_catalog(conn)
        conn.commit()
    return db_path


def load_current_data_version_context(data_root: str | Path = "data") -> dict[str, str | None]:
    """Load the current data foundation manifest context for run artifacts."""
    manifest_path = Path(data_root) / "manifest" / "data_foundation_manifest.json"
    if not manifest_path.exists():
        return {
            "data_version_id": None,
            "data_manifest_path": None,
            "latest_trade_date": None,
        }
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {
        "data_version_id": payload.get("version_id"),
        "data_manifest_path": str(manifest_path),
        "latest_trade_date": payload.get("latest_trade_date"),
    }


def record_quality_check_result(
    *,
    dataset_name: str,
    check_name: str,
    mode: str,
    severity: str,
    status: str,
    details: dict,
    version_id: str | None = None,
    state_dir: str | Path = "state",
) -> str:
    """Record one quality check result in the local metadata store."""
    state_path = Path(state_dir)
    state_path.mkdir(parents=True, exist_ok=True)
    db_path = state_path / "quant_meta.sqlite"
    created_at = datetime.now().isoformat(timespec="seconds")
    check_id = f"{created_at}-{dataset_name}-{check_name}".replace(":", "").replace(" ", "_")
    with sqlite3.connect(db_path) as conn:
        _create_schema(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO quality_check_results
            (check_id, version_id, dataset_name, check_name, mode, severity, status, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                check_id,
                version_id,
                dataset_name,
                check_name,
                mode,
                severity,
                status,
                json.dumps(details, ensure_ascii=False),
                created_at,
            ),
        )
        conn.commit()
    return check_id


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS data_versions (
            version_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            data_root TEXT NOT NULL,
            latest_trade_date TEXT,
            manifest_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS dataset_snapshots (
            version_id TEXT NOT NULL,
            name TEXT NOT NULL,
            layer TEXT NOT NULL,
            source TEXT NOT NULL,
            path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            rows INTEGER,
            date_min TEXT,
            date_max TEXT,
            symbol_count INTEGER,
            pit_status TEXT NOT NULL,
            PRIMARY KEY (version_id, name),
            FOREIGN KEY (version_id) REFERENCES data_versions(version_id)
        );

        CREATE TABLE IF NOT EXISTS quality_check_results (
            check_id TEXT PRIMARY KEY,
            version_id TEXT,
            dataset_name TEXT NOT NULL,
            check_name TEXT NOT NULL,
            mode TEXT NOT NULL,
            severity TEXT NOT NULL,
            status TEXT NOT NULL,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS source_coverage_results (
            coverage_id TEXT PRIMARY KEY,
            version_id TEXT,
            primary_source_id TEXT NOT NULL,
            comparison_source_id TEXT NOT NULL,
            dataset_name TEXT NOT NULL,
            details_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS query_catalog (
            entry_name TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            layer TEXT NOT NULL,
            backing_type TEXT NOT NULL,
            backing_location TEXT NOT NULL,
            recommended_filters TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS run_metadata (
            run_id TEXT PRIMARY KEY,
            run_type TEXT NOT NULL,
            strategy_name TEXT NOT NULL,
            data_version_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            output_paths_json TEXT NOT NULL,
            config_hash TEXT,
            summary_json TEXT NOT NULL,
            FOREIGN KEY (data_version_id) REFERENCES data_versions(version_id)
        );
        """
    )


def _upsert_manifest(conn: sqlite3.Connection, manifest: DataFoundationManifest) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO data_versions
        (version_id, created_at, data_root, latest_trade_date, manifest_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            manifest.version_id,
            manifest.created_at,
            manifest.data_root,
            manifest.latest_trade_date,
            json.dumps(manifest.to_dict(), ensure_ascii=False),
        ),
    )


def _upsert_datasets(conn: sqlite3.Connection, manifest: DataFoundationManifest) -> None:
    for item in manifest.datasets:
        conn.execute(
            """
            INSERT OR REPLACE INTO dataset_snapshots
            (version_id, name, layer, source, path, sha256, rows, date_min, date_max, symbol_count, pit_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                manifest.version_id,
                item.name,
                item.layer,
                item.source,
                item.path,
                item.sha256,
                item.rows,
                item.date_min,
                item.date_max,
                item.symbol_count,
                item.pit_status,
            ),
        )


def _upsert_query_catalog(conn: sqlite3.Connection) -> None:
    for entry in DEFAULT_QUERY_CATALOG:
        conn.execute(
            """
            INSERT OR REPLACE INTO query_catalog
            (entry_name, description, layer, backing_type, backing_location, recommended_filters)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                entry["entry_name"],
                entry["description"],
                entry["layer"],
                entry["backing_type"],
                entry["backing_location"],
                json.dumps(entry["recommended_filters"], ensure_ascii=False),
            ),
        )


DEFAULT_QUERY_CATALOG: tuple[dict[str, object], ...] = (
    {
        "entry_name": "daily_bars_raw_price",
        "description": "原始价格日线，用于执行股数和原始行情检查",
        "layer": "silver",
        "backing_type": "parquet",
        "backing_location": "data/silver/daily_bars_raw_price.parquet",
        "recommended_filters": ["datetime", "vt_symbol"],
    },
    {
        "entry_name": "daily_bars_adjusted",
        "description": "后复权日线，用于收益计算",
        "layer": "silver",
        "backing_type": "parquet",
        "backing_location": "data/silver/daily_bars_adjusted.parquet",
        "recommended_filters": ["datetime", "vt_symbol"],
    },
    {
        "entry_name": "execution_universe",
        "description": "每日可执行股票池",
        "layer": "gold",
        "backing_type": "parquet",
        "backing_location": "data/gold/execution_universe.parquet",
        "recommended_filters": ["datetime", "vt_symbol"],
    },
    {
        "entry_name": "research_universe",
        "description": "每日研究股票池",
        "layer": "gold",
        "backing_type": "parquet",
        "backing_location": "data/gold/research_universe.parquet",
        "recommended_filters": ["datetime", "vt_symbol"],
    },
    {
        "entry_name": "trading_calendar",
        "description": "交易日历",
        "layer": "silver",
        "backing_type": "parquet",
        "backing_location": "data/silver/trading_calendar.parquet",
        "recommended_filters": ["trade_date", "market"],
    },
    {
        "entry_name": "adjust_factors",
        "description": "由未复权和后复权日线推导的复权因子",
        "layer": "silver",
        "backing_type": "parquet",
        "backing_location": "data/silver/adjust_factors.parquet",
        "recommended_filters": ["datetime", "vt_symbol"],
    },
    {
        "entry_name": "corporate_actions_candidates",
        "description": "由复权因子跳变推导的公司行动候选事件",
        "layer": "silver",
        "backing_type": "parquet",
        "backing_location": "data/silver/corporate_actions_candidates.parquet",
        "recommended_filters": ["datetime", "vt_symbol"],
    },
    {
        "entry_name": "limit_status_daily",
        "description": "基于证券快照涨跌停字段生成的日频涨跌停状态",
        "layer": "silver",
        "backing_type": "parquet",
        "backing_location": "data/silver/limit_status_daily.parquet",
        "recommended_filters": ["datetime", "vt_symbol"],
    },
    {
        "entry_name": "suspension_daily",
        "description": "停复牌日频状态，优先来自 daily_status，缺失时使用成交量近似",
        "layer": "silver",
        "backing_type": "parquet",
        "backing_location": "data/silver/suspension_daily.parquet",
        "recommended_filters": ["datetime", "vt_symbol"],
    },
    {
        "entry_name": "st_status_daily",
        "description": "ST 日频状态，优先来自 daily_status，缺失时使用名称快照近似",
        "layer": "silver",
        "backing_type": "parquet",
        "backing_location": "data/silver/st_status_daily.parquet",
        "recommended_filters": ["datetime", "vt_symbol"],
    },
    {
        "entry_name": "data_versions",
        "description": "数据版本记录",
        "layer": "state",
        "backing_type": "metadata_table",
        "backing_location": "state/quant_meta.sqlite:data_versions",
        "recommended_filters": ["version_id", "latest_trade_date"],
    },
    {
        "entry_name": "quality_check_results",
        "description": "质量检查历史",
        "layer": "state",
        "backing_type": "metadata_table",
        "backing_location": "state/quant_meta.sqlite:quality_check_results",
        "recommended_filters": ["version_id", "mode", "status"],
    },
    {
        "entry_name": "run_metadata",
        "description": "策略运行追溯记录",
        "layer": "state",
        "backing_type": "metadata_table",
        "backing_location": "state/quant_meta.sqlite:run_metadata",
        "recommended_filters": ["run_id", "data_version_id"],
    },
)
