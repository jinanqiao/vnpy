"""从 QMT 网关批量下载全 A 日线。

默认下载未复权日线；也可通过 --adjust back_ratio 下载等比后复权日线。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import polars as pl

from vnpy.alpha.research.qmt_gateway_data import QmtGatewayConfig, check_qmt_gateway_health, fetch_qmt_daily_bars

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 QMT 网关批量下载全 A 日线")
    parser.add_argument("--output-path", default="data/silver/daily_bars_raw_price.parquet")
    parser.add_argument("--shard-dir", default="data/raw/qmt/daily_none")
    parser.add_argument("--symbols-path", default="data/universe/all_a_symbols.parquet")
    parser.add_argument("--adjust", default="none", choices=["none", "back_ratio"])
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--count", type=int, default=5000)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="仅下载前 N 只，调试用")
    return parser.parse_args(argv)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    args = parse_args()
    gateway = QmtGatewayConfig.from_env()
    gateway = QmtGatewayConfig(base_url=gateway.base_url, token=gateway.token, timeout=args.timeout)
    try:
        health = check_qmt_gateway_health(gateway)
        logger.info("QMT 网关健康检查通过: %s", health)
    except Exception as exc:
        print(f"QMT 网关不可达: {exc}", file=sys.stderr)
        return 2

    symbols = pl.read_parquet(args.symbols_path).get_column("vt_symbol").unique().sort().to_list()
    if args.limit > 0:
        symbols = symbols[: args.limit]
    failed = _download_batches(gateway, symbols, Path(args.shard_dir), args)
    try:
        merged = _merge_shards(Path(args.shard_dir))
    except Exception as exc:
        print(f"合并分片失败: {exc}", file=sys.stderr)
        return 3

    failed_path = Path("data/quality") / f"failed_symbols_qmt_daily_{args.adjust}.json"
    failed_path.parent.mkdir(parents=True, exist_ok=True)
    failed_path.write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")

    if failed:
        print(f"存在失败股票，正式文件未覆盖。失败清单: {failed_path}", file=sys.stderr)
        return 1

    output = Path(args.output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(".tmp.parquet")
    merged.write_parquet(temp)
    temp.rename(output)
    logger.info("完成: %s 行=%d 股票=%d", output, merged.height, merged["vt_symbol"].n_unique())
    return 0


def _download_batches(config: QmtGatewayConfig, symbols: list[str], shard_dir: Path, args: argparse.Namespace) -> list[str]:
    shard_dir.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    total = (len(symbols) + args.batch_size - 1) // args.batch_size
    for index in range(total):
        shard = shard_dir / f"shard_{index:04d}.parquet"
        if args.resume and shard.exists():
            logger.info("分片已存在，跳过: %s", shard)
            continue
        batch = symbols[index * args.batch_size:(index + 1) * args.batch_size]
        frames: list[pl.DataFrame] = []
        with ThreadPoolExecutor(max_workers=max(args.workers, 1)) as pool:
            for symbol, outcome in pool.map(lambda item: (item, _fetch_one(config, item, args.count, args.adjust)), batch):
                if isinstance(outcome, str):
                    failed.append(f"{symbol}: {outcome}")
                else:
                    frames.append(outcome)
        if frames:
            pl.concat(frames, how="vertical").write_parquet(shard)
        logger.info("批次 %d/%d 完成: 成功 %d/%d，累计失败 %d", index + 1, total, len(frames), len(batch), len(failed))
    return failed


def _fetch_one(config: QmtGatewayConfig, symbol: str, count: int, adjust: str) -> pl.DataFrame | str:
    for attempt in range(2):
        try:
            frame = fetch_qmt_daily_bars(config, symbol, count=count, adjust=adjust)
            if frame.is_empty():
                return "empty"
            bad_rows = frame.filter(
                (pl.col("open") <= 0)
                | (pl.col("high") <= 0)
                | (pl.col("low") <= 0)
                | (pl.col("close") <= 0)
            ).height
            if bad_rows:
                return f"non_positive_ohlc_rows={bad_rows}"
            return frame
        except Exception as exc:
            if attempt == 1:
                return str(exc)
            time.sleep(2)
    return "unknown"


def _merge_shards(shard_dir: Path) -> pl.DataFrame:
    paths = sorted(shard_dir.glob("shard_*.parquet"))
    if not paths:
        raise ValueError(f"{shard_dir} 下没有任何分片")
    merged = (
        pl.concat([pl.read_parquet(path) for path in paths], how="vertical")
        .with_columns(pl.col("datetime").cast(pl.Datetime))
        .unique(subset=["datetime", "vt_symbol"], keep="last")
        .sort(["vt_symbol", "datetime"])
    )
    bad = merged.filter(
        (pl.col("open") <= 0)
        | (pl.col("high") <= 0)
        | (pl.col("low") <= 0)
        | (pl.col("close") <= 0)
    )
    if not bad.is_empty():
        symbols = bad.get_column("vt_symbol").unique().sort().to_list()
        raise ValueError(f"分片包含非正 OHLC，股票数={len(symbols)}，样例={symbols[:10]}")
    return merged


if __name__ == "__main__":
    raise SystemExit(main())
