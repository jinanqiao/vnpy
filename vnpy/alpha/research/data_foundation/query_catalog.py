from __future__ import annotations

from pathlib import Path

from .metadata import DEFAULT_QUERY_CATALOG


def write_query_catalog_sql(path: str | Path = "state/query_catalog.sql") -> Path:
    """Write DuckDB-compatible view definitions for parquet-backed datasets."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "-- 数据底座查询目录，供 DuckDB 或兼容工具执行。",
        "-- 大表查询建议始终带 datetime / vt_symbol 过滤条件。",
        "",
    ]
    for entry in DEFAULT_QUERY_CATALOG:
        if entry["backing_type"] != "parquet":
            continue
        name = str(entry["entry_name"])
        location = str(entry["backing_location"])
        lines.append(f"CREATE OR REPLACE VIEW {name} AS")
        lines.append(f"SELECT * FROM read_parquet('{location}');")
        lines.append("")
    output.write_text("\n".join(lines), encoding="utf-8")
    return output
