"""从 QMT 网关下载申万一级行业成分表。

产物: data/sector/sw1_members.parquet
列结构对齐 sector_members.parquet 的核心列: sector / vt_symbol / code / exchange / category / source / snapshot_at

用法:
    python3 scripts/download_sw1_members.py
    python3 scripts/download_sw1_members.py --timeout 120
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vnpy.alpha.research.qmt_gateway_data import QmtGatewayConfig, fetch_qmt_symbols


def list_sw1_sectors(data_dir: Path) -> list[str]:
    """从 sectors.parquet 里挑出全市场申万一级板块（不带指数前缀、不含加权口径）。"""
    sectors = pl.read_parquet(data_dir / "sector" / "sectors.parquet")
    names = sectors.get_column("sector").to_list()
    return sorted(
        name for name in names
        if name.startswith("SW1") and not name.endswith("加权")
    )


def fetch_with_retry(config: QmtGatewayConfig, sector: str, retries: int = 3) -> list[dict]:
    for attempt in range(1, retries + 1):
        try:
            return fetch_qmt_symbols(config, sector=sector, limit=5000, detail=False)
        except Exception as exc:  # noqa: BLE001 - 网关冷启动/网络抖动都值得重试
            print(f"  [{sector}] 第 {attempt} 次失败: {type(exc).__name__} {str(exc)[:80]}")
            if attempt == retries:
                raise
            time.sleep(5)
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    base = QmtGatewayConfig.from_env()
    config = QmtGatewayConfig(base_url=base.base_url, token=base.token, timeout=args.timeout)

    sw1_sectors = list_sw1_sectors(data_dir)
    print(f"共 {len(sw1_sectors)} 个申万一级板块待下载")

    snapshot_at = datetime.now()
    rows: list[dict] = []
    failed: list[str] = []
    for i, sector in enumerate(sw1_sectors, 1):
        try:
            symbols = fetch_with_retry(config, sector)
        except Exception:
            failed.append(sector)
            continue
        for item in symbols:
            rows.append(
                {
                    "sector": sector,
                    "vt_symbol": item["symbol"],
                    "code": item.get("code", ""),
                    "exchange": item.get("exchange", ""),
                    "category": "sw_level_1",
                    "source": "qmt_gateway",
                    "snapshot_at": snapshot_at,
                }
            )
        print(f"[{i}/{len(sw1_sectors)}] {sector}: {len(symbols)} 只")

    if failed:
        print(f"失败板块: {failed}")
        return 1

    frame = pl.DataFrame(rows).sort(["sector", "vt_symbol"])
    out_path = data_dir / "sector" / "sw1_members.parquet"
    frame.write_parquet(out_path)
    print(f"已写入 {out_path}: {len(frame)} 行, {frame.get_column('sector').n_unique()} 个行业")

    dup = frame.group_by("vt_symbol").len().filter(pl.col("len") > 1)
    print(f"归属多个行业的股票数: {len(dup)}（申万分类应为 0）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
