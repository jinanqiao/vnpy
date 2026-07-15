"""把本地数据版本元数据同步到 PostgreSQL/TimescaleDB。

该脚本不依赖本机安装 psql 或 psycopg，直接使用 `vnpy-timescaledb`
容器内的 psql。历史大表仍保留在 Parquet，数据库只保存版本、
数据集快照和质量检查结果。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="data", help="数据根目录")
    parser.add_argument("--state-dir", default="state", help="SQLite 元数据目录")
    parser.add_argument("--env-file", default=".env", help="本地数据库环境变量文件")
    parser.add_argument("--container", default="", help="TimescaleDB 容器名，默认读取 POSTGRES_CONTAINER")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env = _load_env(Path(args.env_file))
    container = args.container or env.get("POSTGRES_CONTAINER", "vnpy-timescaledb")
    user = env.get("POSTGRES_USER", "vnpy")
    database = env.get("POSTGRES_DB", "vnpy_quant")

    manifest_path = Path(args.data_root) / "manifest" / "data_foundation_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"缺少 manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    statements = [_upsert_data_version(manifest), *_upsert_dataset_snapshots(manifest)]
    statements.extend(_quality_check_statements(Path(args.state_dir) / "quant_meta.sqlite"))
    sql = "BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;\n"

    subprocess.run(
        ["docker", "exec", "-i", container, "psql", "-U", user, "-d", database, "-v", "ON_ERROR_STOP=1"],
        input=sql.encode("utf-8"),
        check=True,
    )
    print(json.dumps({"status": "pass", "version_id": manifest.get("version_id"), "statements": len(statements)}, ensure_ascii=False, indent=2))
    return 0


def _load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def _upsert_data_version(manifest: dict[str, Any]) -> str:
    return f"""
INSERT INTO ops.data_versions (version_id, created_at, data_root, latest_trade_date, manifest_json)
VALUES (
  {_sql_text(manifest.get("version_id"))},
  {_sql_text(manifest.get("created_at"))}::timestamptz,
  {_sql_text(manifest.get("data_root"))},
  {_sql_text(manifest.get("latest_trade_date"))}::date,
  {_sql_json(manifest)}
)
ON CONFLICT (version_id) DO UPDATE SET
  created_at = EXCLUDED.created_at,
  data_root = EXCLUDED.data_root,
  latest_trade_date = EXCLUDED.latest_trade_date,
  manifest_json = EXCLUDED.manifest_json;
"""


def _upsert_dataset_snapshots(manifest: dict[str, Any]) -> list[str]:
    version_id = manifest.get("version_id")
    statements: list[str] = []
    for item in manifest.get("datasets", []):
        statements.append(
            f"""
INSERT INTO ops.dataset_snapshots
(version_id, name, layer, source, path, sha256, rows, date_min, date_max, symbol_count, pit_status)
VALUES (
  {_sql_text(version_id)},
  {_sql_text(item.get("name"))},
  {_sql_text(item.get("layer"))},
  {_sql_text(item.get("source"))},
  {_sql_text(item.get("path"))},
  {_sql_text(item.get("sha256"))},
  {_sql_int(item.get("rows"))},
  {_sql_text(item.get("date_min"))}::date,
  {_sql_text(item.get("date_max"))}::date,
  {_sql_int(item.get("symbol_count"))},
  {_sql_text(item.get("pit_status"))}
)
ON CONFLICT (version_id, name) DO UPDATE SET
  layer = EXCLUDED.layer,
  source = EXCLUDED.source,
  path = EXCLUDED.path,
  sha256 = EXCLUDED.sha256,
  rows = EXCLUDED.rows,
  date_min = EXCLUDED.date_min,
  date_max = EXCLUDED.date_max,
  symbol_count = EXCLUDED.symbol_count,
  pit_status = EXCLUDED.pit_status;
"""
        )
    return statements


def _quality_check_statements(sqlite_path: Path) -> list[str]:
    if not sqlite_path.exists():
        return []
    statements: list[str] = []
    with sqlite3.connect(sqlite_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT check_id, version_id, dataset_name, check_name, mode, severity, status, details_json, created_at
            FROM quality_check_results
            """
        ).fetchall()
    for row in rows:
        details = json.loads(row["details_json"])
        statements.append(
            f"""
INSERT INTO ops.quality_check_results
(check_id, version_id, dataset_name, check_name, mode, severity, status, details_json, created_at)
VALUES (
  {_sql_text(row["check_id"])},
  {_sql_text(row["version_id"])},
  {_sql_text(row["dataset_name"])},
  {_sql_text(row["check_name"])},
  {_sql_text(row["mode"])},
  {_sql_text(row["severity"])},
  {_sql_text(row["status"])},
  {_sql_json(details)},
  {_sql_text(row["created_at"])}::timestamptz
)
ON CONFLICT (check_id) DO UPDATE SET
  version_id = EXCLUDED.version_id,
  dataset_name = EXCLUDED.dataset_name,
  check_name = EXCLUDED.check_name,
  mode = EXCLUDED.mode,
  severity = EXCLUDED.severity,
  status = EXCLUDED.status,
  details_json = EXCLUDED.details_json,
  created_at = EXCLUDED.created_at;
"""
        )
    return statements


def _sql_text(value: Any) -> str:
    if value is None or value == "":
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _sql_int(value: Any) -> str:
    if value is None or value == "":
        return "NULL"
    return str(int(value))


def _sql_json(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False).replace("'", "''")
    return f"'{text}'::jsonb"


if __name__ == "__main__":
    raise SystemExit(main())
