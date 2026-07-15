"""刷新实盘数据底座的辅助数据。

本脚本只刷新相对轻量、但会影响实盘判断的辅助表：

- silver/benchmark_index_daily.parquet
- silver/instrument_master_snapshot.parquet
- silver/sector_members_snapshot.parquet
- silver/sw1_members_snapshot.parquet

日线大表仍由 refresh_live_data_foundation.py 负责。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from vnpy.alpha.research.data_foundation.layout import mapping_specs
from vnpy.alpha.research.data_foundation.manifest import build_manifest, write_manifest_files, write_manifest_report
from vnpy.alpha.research.qmt_gateway_data import QmtGatewayConfig, _request, fetch_qmt_daily_bars, fetch_qmt_symbols
from vnpy.alpha.research.turtle_data_lake import INDEX_SYMBOLS, symbols_to_frame


BROAD_SECTORS: tuple[str, ...] = (
    "沪深A股",
    "沪深京A股",
    "上证A股",
    "深证A股",
    "京市A股",
    "创业板",
    "科创板",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--benchmark-count", type=int, default=5000)
    parser.add_argument("--symbol-limit", type=int, default=8000)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--skip-sw1", action="store_true", help="跳过申万一级成分刷新")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.data_root)
    for folder in ["silver", "quality", "manifest"]:
        (root / folder).mkdir(parents=True, exist_ok=True)

    base = QmtGatewayConfig.from_env()
    config = QmtGatewayConfig(base_url=base.base_url, token=base.token, timeout=args.timeout)
    summary: dict[str, Any] = {"started_at": datetime.now().isoformat(timespec="seconds")}

    benchmark = _download_benchmark(config, args.benchmark_count)
    benchmark.write_parquet(root / "silver" / "benchmark_index_daily.parquet")
    summary["benchmark_rows"] = benchmark.height
    summary["benchmark_latest"] = _date_max(benchmark, "datetime")

    symbols = fetch_qmt_symbols(config, sector="沪深A股", limit=args.symbol_limit, detail=True)
    instruments = symbols_to_frame(symbols, "all_a")
    instruments.write_parquet(root / "silver" / "instrument_master_snapshot.parquet")
    summary["instrument_rows"] = instruments.height

    sectors = _download_sectors(config)
    sector_members = _download_sector_members(config, sectors, args.symbol_limit, include_sw1=False)
    sector_members.write_parquet(root / "silver" / "sector_members_snapshot.parquet")
    summary["sector_member_rows"] = sector_members.height

    if not args.skip_sw1:
        sw1_members = _download_sector_members(config, sectors, args.symbol_limit, include_sw1=True)
        sw1_members.write_parquet(root / "silver" / "sw1_members_snapshot.parquet")
        summary["sw1_member_rows"] = sw1_members.height

    manifest = build_manifest(root, mapping_specs(), version_label="data-foundation")
    current_path, version_path = write_manifest_files(manifest, root / "manifest")
    write_manifest_report(manifest, root / "quality" / "data_foundation_manifest.md")
    summary.update(
        {
            "status": "pass",
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "manifest_path": str(current_path),
            "manifest_version_path": str(version_path),
            "data_version_id": manifest.version_id,
        }
    )
    summary_path = root / "quality" / "auxiliary_refresh_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _download_benchmark(config: QmtGatewayConfig, count: int) -> pl.DataFrame:
    frames: list[pl.DataFrame] = []
    for symbol in INDEX_SYMBOLS:
        frame = fetch_qmt_daily_bars(config, symbol, count=count, adjust="none")
        if frame.is_empty():
            continue
        frames.append(frame.rename({"vt_symbol": "index_symbol"}))
    if not frames:
        raise RuntimeError("QMT Gateway 未返回任何基准指数日线")
    return pl.concat(frames, how="vertical").sort(["index_symbol", "datetime"])


def _download_sectors(config: QmtGatewayConfig) -> list[str]:
    data = _request(config, "GET", "/market/sectors")
    sectors = [str(item) for item in data.get("sectors") or []]
    return sorted(dict.fromkeys(sectors))


def _download_sector_members(
    config: QmtGatewayConfig,
    sectors: list[str],
    limit: int,
    *,
    include_sw1: bool,
) -> pl.DataFrame:
    if include_sw1:
        selected = [name for name in sectors if name.startswith("SW1") and not name.endswith("加权")]
        category = "sw_level_1"
    else:
        selected = [name for name in BROAD_SECTORS if name in sectors]
        selected.extend([name for name in sectors if name.startswith("GICS1")])
        selected = list(dict.fromkeys(selected))
        category = "qmt_sector_or_gics_level_1"

    snapshot_at = datetime.now()
    rows: list[dict[str, Any]] = []
    for sector in selected:
        symbols = fetch_qmt_symbols(config, sector=sector, limit=limit, detail=False)
        for item in symbols:
            rows.append(
                {
                    "sector": sector,
                    "vt_symbol": str(item.get("symbol") or ""),
                    "code": str(item.get("code") or ""),
                    "exchange": str(item.get("exchange") or ""),
                    "category": category,
                    "source": "qmt_gateway",
                    "snapshot_at": snapshot_at,
                }
            )
        print(f"{sector}: {len(symbols)}", flush=True)
    if not rows:
        raise RuntimeError("QMT Gateway 未返回任何行业成分")
    return pl.DataFrame(rows).filter(pl.col("vt_symbol") != "").sort(["sector", "vt_symbol"])


def _date_max(df: pl.DataFrame, column: str) -> str | None:
    if df.is_empty() or column not in df.columns:
        return None
    value = df.select(pl.col(column).cast(pl.Date).max()).item()
    return str(value) if value is not None else None


if __name__ == "__main__":
    raise SystemExit(main())
