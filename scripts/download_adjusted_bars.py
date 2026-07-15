"""数据湖升级 CLI：从 QMT 网关下载全 A 后复权日线。

为什么需要这份数据：数据湖现有行情未复权，分红除权日价格会"假性大跌"，
直接用来算回测收益会把每次分红当成亏损。后复权价格把历史收益率还原为
真实持有收益（隐含分红自动再投资），是回测的收益计算基准。

用法（在仓库根目录执行）：

    python scripts/download_adjusted_bars.py --resume

行为契约见 specs/002-mainline-backtest/contracts/cli-contract.md：
    - 启动先做网关健康检查，失败退出码 2 并给出排查提示；
    - 分批下载写分片，--resume 跳过已完成的批次（断点续传）；
    - 单只失败重试一次，仍失败记入失败清单，不中断整体；
    - 全部完成后合并分片 → 与未复权数据交叉校验 → 通过才写最终文件（失败退出码 3）。
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

from vnpy.alpha.research.mainline_backtest.data_loader import verify_adjusted_bars
from vnpy.alpha.research.qmt_gateway_data import (
    QmtGatewayConfig,
    check_qmt_gateway_health,
    fetch_qmt_daily_bars,
)

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="下载全 A 后复权日线到数据湖")
    parser.add_argument("--output-path", default="data/silver/daily_bars_adjusted.parquet")
    parser.add_argument("--shard-dir", default="data/raw/qmt/adjusted", help="分片暂存目录")
    parser.add_argument("--symbols-path", default="data/universe/all_a_symbols.parquet")
    parser.add_argument("--unadjusted-path", default="data/silver/daily_bars_raw_price.parquet")
    parser.add_argument("--batch-size", type=int, default=200, help="每批下载的股票数")
    parser.add_argument("--count", type=int, default=5000, help="每只回溯的 K 线根数")
    parser.add_argument("--timeout", type=float, default=90.0, help="单请求超时（秒）")
    parser.add_argument("--resume", action="store_true", help="跳过已存在的分片（断点续传）")
    parser.add_argument("--workers", type=int, default=8, help="批内并发请求数（1 = 串行）")
    parser.add_argument("--skip-verify", action="store_true", help="跳过一致性校验（调试用）")
    return parser.parse_args(argv)


def fetch_one_with_retry(config: QmtGatewayConfig, symbol: str, count: int) -> pl.DataFrame:
    """下载一只股票的等比后复权日线，失败自动重试一次（网关冷启动首请求容易超时）。

    注意必须用 back_ratio（等比后复权）而不是 back（等额后复权）：
    等额复权对高分红股票会把历史价格调成负数，算收益率是错的；
    等比复权保持每日涨跌幅不变且价格恒为正，才是收益计算的正确口径。
    """
    try:
        return fetch_qmt_daily_bars(config, symbol, count=count, adjust="back_ratio")
    except Exception:
        time.sleep(2)
        return fetch_qmt_daily_bars(config, symbol, count=count, adjust="back_ratio")


def download_batches(
    config: QmtGatewayConfig,
    symbols: list[str],
    shard_dir: Path,
    batch_size: int,
    count: int,
    resume: bool,
    workers: int = 1,
) -> list[str]:
    """分批下载并写分片 parquet，返回失败股票清单（格式 "代码: 原因"）。

    workers > 1 时批内用线程池并发请求（每只股票一个独立 HTTP 请求，互不依赖）；
    并发数不宜过大，QMT 网关是单实例服务，压太狠反而超时更多。
    """
    shard_dir.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    total_batches = (len(symbols) + batch_size - 1) // batch_size

    for batch_index in range(total_batches):
        shard_path = shard_dir / f"shard_{batch_index:04d}.parquet"
        if resume and shard_path.exists():
            logger.info("批次 %d/%d 已存在，跳过", batch_index + 1, total_batches)
            continue

        batch = symbols[batch_index * batch_size:(batch_index + 1) * batch_size]
        frames: list[pl.DataFrame] = []
        with ThreadPoolExecutor(max_workers=max(workers, 1)) as pool:
            results = pool.map(
                lambda symbol: (symbol, _fetch_or_error(config, symbol, count)), batch
            )
            for symbol, outcome in results:
                if isinstance(outcome, str):
                    failed.append(f"{symbol}: {outcome}")
                else:
                    frames.append(outcome)

        if frames:
            pl.concat(frames, how="vertical").write_parquet(shard_path)
        logger.info(
            "批次 %d/%d 完成: 成功 %d / %d 只，累计失败 %d",
            batch_index + 1, total_batches, len(frames), len(batch), len(failed),
        )
    return failed


def _fetch_or_error(config: QmtGatewayConfig, symbol: str, count: int) -> pl.DataFrame | str:
    """下载单只股票；异常/空数据时返回错误字符串（线程池里不抛异常，便于统计）。"""
    try:
        frame = fetch_one_with_retry(config, symbol, count)
    except Exception as error:
        return str(error)
    return frame if not frame.is_empty() else "empty"


def merge_shards(shard_dir: Path) -> tuple[pl.DataFrame, list[str]]:
    """合并全部分片并剔除无效行，返回 (合并结果, 被整只剔除的股票清单)。

    个别退市股在 QMT 等比复权模式下会返回全 0 的价格和成交额（复权因子缺失），
    这种行留着必然污染回测，这里按 close <= 0 直接剔除；
    整只股票都被剔除的记入返回值，由调用方并入失败清单。
    """
    shard_paths = sorted(shard_dir.glob("shard_*.parquet"))
    if not shard_paths:
        raise ValueError(f"{shard_dir} 下没有任何分片，下载可能全部失败")
    merged = pl.concat([pl.read_parquet(path) for path in shard_paths], how="vertical")
    merged = merged.with_columns(pl.col("datetime").cast(pl.Datetime)).sort(["vt_symbol", "datetime"])

    symbols_before = set(merged.get_column("vt_symbol").unique().to_list())
    cleaned = merged.filter(pl.col("close") > 0)
    dropped_symbols = sorted(symbols_before - set(cleaned.get_column("vt_symbol").unique().to_list()))
    dropped_rows = merged.height - cleaned.height
    if dropped_rows:
        logger.warning(
            "剔除无效行（close<=0，复权因子缺失）: %d 行；整只剔除: %s",
            dropped_rows, dropped_symbols or "无",
        )
    return cleaned, [f"{symbol}: invalid_zero_price" for symbol in dropped_symbols]


def write_verification_report(result: dict, failed_count: int, path: Path) -> None:
    """把校验结果写成人读的 Markdown 报告。"""
    lines = [
        "# 后复权行情校验报告",
        "",
        f"- 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 下载失败股票数: {failed_count}",
        "",
        "| 校验项 | 结果 | 数值 |",
        "|---|---|---|",
        f"| 覆盖率 >= 99% | {'通过' if result['coverage_ok'] else '不通过'} | {result['coverage']:.2%} |",
        f"| 成交额一致 | {'通过' if result['turnover_ok'] else '不通过'} | 异常 {result['turnover_mismatch_rows']} 行 |",
        f"| 复权比例只升不降 | {'通过' if result['ratio_ok'] else '不通过'} | 回落 {result['ratio_drop_rows']} 行 |",
        "",
        f"**总体: {'通过' if result['passed'] else '不通过'}**",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    args = parse_args()
    gateway = QmtGatewayConfig.from_env()
    gateway = QmtGatewayConfig(base_url=gateway.base_url, token=gateway.token, timeout=args.timeout)

    try:
        health = check_qmt_gateway_health(gateway)
        logger.info("网关健康检查通过: %s", health)
    except Exception as error:
        print(
            f"QMT 网关不可达: {error}\n"
            f"排查提示: 1) 确认阿里云实例运行中 2) 安全组放行 8710 端口 "
            f"3) 公网 IP 变化时设置环境变量 QMT_GATEWAY_URL（当前 {gateway.base_url}）",
            file=sys.stderr,
        )
        return 2

    symbols = (
        pl.read_parquet(args.symbols_path).get_column("vt_symbol").unique().sort().to_list()
    )
    logger.info("待下载 %d 只股票，每批 %d 只", len(symbols), args.batch_size)

    failed = download_batches(
        gateway, symbols, Path(args.shard_dir), args.batch_size, args.count, args.resume,
        workers=args.workers,
    )

    merged, dropped = merge_shards(Path(args.shard_dir))
    failed.extend(dropped)
    failed_path = Path("data/quality/failed_symbols_adjusted.json")
    failed_path.parent.mkdir(parents=True, exist_ok=True)
    failed_path.write_text(json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("合并完成: %d 行, %d 只股票", merged.height, merged["vt_symbol"].n_unique())

    if not args.skip_verify:
        unadjusted = pl.read_parquet(args.unadjusted_path).with_columns(
            pl.col("datetime").cast(pl.Datetime)
        )
        result = verify_adjusted_bars(
            merged.with_columns(pl.col("datetime").cast(pl.Date)),
            unadjusted.with_columns(pl.col("datetime").cast(pl.Date)),
        )
        write_verification_report(result, len(failed), Path("data/quality/adjusted_bars_verification.md"))
        logger.info("校验结果: %s", result)
        if not result["passed"]:
            print("一致性校验不通过，最终文件未写入。详见 data/quality/adjusted_bars_verification.md", file=sys.stderr)
            return 3

    # 校验通过后原子写入：先写临时文件再改名，避免中断留下半个文件
    output_path = Path(args.output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_suffix(".tmp.parquet")
    merged.write_parquet(temp_path)
    temp_path.rename(output_path)
    logger.info("完成: 后复权行情已写入 %s（失败 %d 只，见 %s）", output_path, len(failed), failed_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
