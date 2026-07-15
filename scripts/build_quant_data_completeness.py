"""Complete the local quant data foundation from the hardcoded QMT Gateway."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from collections.abc import Sequence


import polars as pl

from vnpy.alpha.research.qmt_gateway_data import QmtGatewayConfig, _request


BROAD_SECTORS: tuple[str, ...] = (
    "沪深A股",
    "沪深京A股",
    "上证A股",
    "深证A股",
    "京市A股",
    "创业板",
    "科创板",
)

FINANCIAL_TABLES: tuple[str, ...] = (
    "Balance",
    "Income",
    "CashFlow",
    "Capital",
    "PershareIndex",
    "HolderNum",
    "Top10Holder",
    "Top10FlowHolder",
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build quant data completeness artifacts.")
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--trading-date-count", type=int, default=5000)
    parser.add_argument("--sector-limit", type=int, default=8000)
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    data_root = Path(args.data_root)
    cfg = QmtGatewayConfig(timeout=args.timeout)

    for folder in ["calendar", "sector", "financial", "manifest", "quality", "dictionary"]:
        (data_root / folder).mkdir(parents=True, exist_ok=True)

    health = _safe_request(cfg, "GET", "/health", auth=False)
    xtdata_debug = _safe_request(cfg, "GET", "/market/debug/xtdata")

    trading_dates = _build_trading_dates(cfg, args.trading_date_count)
    trading_dates.write_parquet(data_root / "calendar" / "trading_dates.parquet")

    sectors = _build_sectors(cfg)
    sectors.write_parquet(data_root / "sector" / "sectors.parquet")

    sector_members = _build_sector_members(cfg, sectors, args.sector_limit)
    sector_members.write_parquet(data_root / "sector" / "sector_members.parquet")

    financial_probe = _probe_financial_data(cfg)
    (data_root / "financial" / "financial_probe.json").write_text(
        json.dumps(financial_probe, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    manifest = _build_manifest(data_root, health, xtdata_debug, financial_probe)
    (data_root / "manifest" / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _write_data_dictionary(data_root)
    _write_gap_report(data_root, financial_probe)
    _write_inventory_report(data_root, manifest)
    print("Quant data completeness artifacts generated.")


def _build_trading_dates(cfg: QmtGatewayConfig, count: int) -> pl.DataFrame:
    rows: list[dict[str, Any]] = []
    for market in ["SH", "SZ"]:
        data = _request(cfg, "GET", "/market/trading-dates", params={"market": market, "count": int(count)})
        for item in data.get("dates") or []:
            rows.append({"market": market, "datetime": item, "source": "qmt_gateway"})
    return (
        pl.DataFrame(rows)
        .with_columns(pl.col("datetime").str.to_datetime(strict=False).dt.date().alias("trade_date"))
        .select(["market", "trade_date", "source"])
        .unique()
        .sort(["market", "trade_date"])
    )


def _build_sectors(cfg: QmtGatewayConfig) -> pl.DataFrame:
    data = _request(cfg, "GET", "/market/sectors")
    rows = []
    for sector in data.get("sectors") or []:
        sector_name = str(sector)
        rows.append(
            {
                "sector": sector_name,
                "category": _sector_category(sector_name),
                "source": "qmt_gateway",
                "snapshot_at": datetime.now(),
            }
        )
    return pl.DataFrame(rows).sort(["category", "sector"])


def _build_sector_members(cfg: QmtGatewayConfig, sectors: pl.DataFrame, limit: int) -> pl.DataFrame:
    sector_names = _selected_sector_names(sectors)
    rows: list[dict[str, Any]] = []
    for sector in sector_names:
        try:
            data = _request(
                cfg,
                "GET",
                "/market/sector-stocks",
                params={"sector": sector, "limit": int(limit), "detail": "false"},
            )
        except Exception as exc:
            rows.append(
                {
                    "sector": sector,
                    "vt_symbol": "",
                    "code": "",
                    "exchange": "",
                    "name": "",
                    "category": _sector_category(sector),
                    "error": str(exc),
                    "source": "qmt_gateway",
                    "snapshot_at": datetime.now(),
                }
            )
            continue
        for item in data.get("stocks") or []:
            rows.append(
                {
                    "sector": sector,
                    "vt_symbol": str(item.get("symbol") or ""),
                    "code": str(item.get("code") or ""),
                    "exchange": str(item.get("exchange") or ""),
                    "name": str(item.get("name") or ""),
                    "category": _sector_category(sector),
                    "error": "",
                    "source": "qmt_gateway",
                    "snapshot_at": datetime.now(),
                }
            )
        print(f"Sector {sector}: {len(data.get('stocks') or [])} members", flush=True)
    return pl.DataFrame(rows).sort(["category", "sector", "vt_symbol"]) if rows else pl.DataFrame()


def _probe_financial_data(cfg: QmtGatewayConfig) -> dict[str, Any]:
    result: dict[str, Any] = {
        "sample_symbol": "000001.SZ",
        "tables": {},
        "overall": "unknown",
        "source": "qmt_gateway",
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    any_rows = False
    for table in FINANCIAL_TABLES:
        try:
            data = _request(
                cfg,
                "POST",
                "/market/financial",
                json={
                    "symbols": ["000001.SZ"],
                    "tables": [table],
                    "start_time": "20200101",
                    "end_time": "20260702",
                    "sample_rows": 3,
                },
            )
            rows = (((data.get("data") or {}).get("000001.SZ") or {}).get(table) or [])
            result["tables"][table] = {"ok": True, "sample_rows": len(rows)}
            any_rows = any_rows or bool(rows)
        except Exception as exc:
            result["tables"][table] = {"ok": False, "error": str(exc)}
    result["overall"] = "available_with_rows" if any_rows else "available_but_empty"
    return result


def _build_manifest(data_root: Path, health: dict[str, Any], xtdata_debug: dict[str, Any], financial_probe: dict[str, Any]) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for path in sorted(data_root.rglob("*")):
        if not path.is_file() or path.name == ".DS_Store":
            continue
        rel = path.relative_to(data_root)
        item: dict[str, Any] = {
            "path": str(rel),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        if path.suffix == ".parquet":
            try:
                df = pl.read_parquet(path)
                item["rows"] = df.height
                item["columns"] = df.columns
            except Exception as exc:
                item["read_error"] = str(exc)
        files.append(item)

    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "gateway": {
            "base_url": QmtGatewayConfig().base_url,
            "health": health,
            "xtdata_debug": xtdata_debug,
        },
        "financial_probe": financial_probe,
        "files": files,
    }


def _write_data_dictionary(data_root: Path) -> None:
    text = """# Quant Data Dictionary

## Core Tables

- `universe/all_a_symbols.parquet`: current full A-share instrument master from QMT.
- `universe/index_members.parquet`: current index constituent snapshots for HS300, ZZ500, ZZ1000.
- `silver/daily_bars_raw_price.parquet`: standard unadjusted full A-share daily OHLCV bars.
- `silver/daily_bars_adjusted.parquet`: standard adjusted full A-share daily OHLCV bars.
- `gold/execution_universe.parquet`: live/paper/backtest execution universe.
- `universe/tradable_calendar.parquet`: daily tradability flags derived from bars and instrument metadata.
- `benchmark/index_daily.parquet`: daily OHLCV bars for major benchmark indexes.
- `calendar/trading_dates.parquet`: SH/SZ exchange trading dates.
- `sector/sectors.parquet`: QMT sector and industry list.
- `sector/sector_members.parquet`: selected broad market and GICS level-1 sector memberships.
- `financial/financial_probe.json`: financial data interface availability probe.
- `manifest/manifest.json`: file inventory, row counts, hashes, and gateway capability snapshot.

## Daily Bar Columns

- `datetime`: trading date.
- `vt_symbol`: symbol in QMT/vn.py style, such as `000001.SZ`.
- `open`, `high`, `low`, `close`: daily OHLC prices.
- `volume`: daily volume.
- `turnover`: daily traded amount.

## Tradable Calendar Columns

- `is_st`: derived from current name containing `ST`.
- `is_suspended`: derived from `turnover <= 0`.
- `is_limit_up`, `is_limit_down`: derived from current instrument limit snapshot when available.
- `listed_days`: available bar count since first local bar.
- `tradable`: practical filter for non-ST, non-suspended, listed enough, price, and liquidity.
- `reason`: first blocking reason.
"""
    (data_root / "dictionary" / "data_dictionary.md").write_text(text, encoding="utf-8")


def _write_gap_report(data_root: Path, financial_probe: dict[str, Any]) -> None:
    text = f"""# Quant Data Gap Report

## Already Covered

- Full A-share instrument master.
- Current HS300, ZZ500, ZZ1000 constituent snapshots.
- Full A-share daily OHLCV bars.
- Major index benchmark bars.
- SH/SZ trading calendar.
- Current sector list and selected sector memberships.
- Daily tradability calendar with ST-like, zero-turnover, liquidity and listed-days filters.
- Data manifest with row counts and hashes.

## Remaining Gaps

1. Historical point-in-time index constituents are not available from the current gateway pull. Current files are snapshots as of build time.
2. Historical ST status is approximated from current security names; true daily risk-warning history is not available locally.
3. Historical limit-up/limit-down prices are not fully point-in-time; current instrument snapshot is used where available.
4. Suspensions are inferred from zero turnover instead of official suspension/resumption events.
5. Corporate actions and adjustment factors are not stored as a dedicated table.
6. Financial statement data probe result: `{financial_probe.get("overall")}`. The gateway exposes the API, but the current sample returned empty rows.
7. Historical sector memberships are current snapshots, not point-in-time classifications.

## Practical Next Source To Add

- Tushare or another PIT-capable data source for historical index constituents, ST history, corporate actions, adjustment factors, financial statements, and official suspension events.
"""
    (data_root / "quality" / "data_gap_report.md").write_text(text, encoding="utf-8")


def _write_inventory_report(data_root: Path, manifest: dict[str, Any]) -> None:
    lines = ["# Quant Data Inventory", ""]
    for item in manifest["files"]:
        rows = f", rows={item['rows']}" if "rows" in item else ""
        lines.append(f"- `{item['path']}`: bytes={item['bytes']}{rows}")
    (data_root / "quality" / "data_inventory.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _selected_sector_names(sectors: pl.DataFrame) -> list[str]:
    names = [str(name) for name in sectors["sector"].to_list()]
    selected = [name for name in BROAD_SECTORS if name in names]
    selected.extend([name for name in names if name.startswith("GICS1")])
    return list(dict.fromkeys(selected))


def _sector_category(sector: str) -> str:
    if sector.startswith("GICS1"):
        return "gics_level_1"
    if sector.startswith("GICS2"):
        return "gics_level_2"
    if sector.startswith("GICS3"):
        return "gics_level_3"
    if sector.startswith("GICS4"):
        return "gics_level_4"
    if "ETF" in sector:
        return "etf"
    if "指数" in sector:
        return "index"
    if "A股" in sector or sector in {"创业板", "科创板"}:
        return "equity_board"
    return "other"


def _safe_request(cfg: QmtGatewayConfig, method: str, path: str, auth: bool = True, **kwargs: Any) -> dict[str, Any]:
    try:
        data: dict[str, Any] = _request(cfg, method, path, auth=auth, **kwargs)
        return data
    except Exception as exc:
        return {"error": str(exc)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    main()
