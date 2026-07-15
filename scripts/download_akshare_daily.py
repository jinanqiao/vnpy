"""从 akshare 下载全 A 的每日流通股本 + 真换手率（T5 数据源）。

背景：QMT 网关未暴露市值/换手率字段（`docs/factor_enhancement_handoff.md`），
用户不改 Windows 侧，因此走 akshare `stock_zh_a_daily` —— 该接口每行返回：
    date / open / high / low / close / volume / amount / outstanding_share / turnover
其中 outstanding_share = 当日流通股本、turnover = 当日换手率（已为小数比例）。
用后复权 (adjust='hfq') 让价格与本仓库 `daily_bars_all_a_adjusted.parquet` 对齐。

产物：`data/akshare/daily_bars_outstanding.parquet`，字段：
    datetime, vt_symbol, close_hfq, outstanding_share, turnover_rate, amount
后续脚本用 `outstanding_share * close_hfq` 近似流通市值（等比后复权，不影响截面排序）。

运行环境（macOS/Framework Python 坑，见 data/akshare_probe.md）：
    export SSL_CERT_FILE=$(python3 -c 'import certifi; print(certifi.where())')
    arch -arm64 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
        scripts/download_akshare_daily.py [--start 20180101] [--workers 6]

支持断点续跑：进度落盘 `data/akshare/_progress.json`，每 200 只保存一次分片，
最后合并成一个 parquet。中途中断再次运行会跳过已完成的股票。
"""
from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import polars as pl

# akshare 内部用 py_mini_racer 做 JS 解密（V8 引擎），多线程共享会触发 macOS
# native crash（libmini_racer + address_pool_manager 冲突）。必须用多进程隔离；
# ProcessPoolExecutor 每个 worker 独立 python 解释器，V8 状态互不影响。
# 5000 只 x 8 年在 workers=6 下预计 60~90 分钟。

# akshare 走 pip 安装的公网 API，Framework Python 走 x86_64 会握手失败，
# 加上 SSL_CERT_FILE 缺失也会 CA 校验失败 —— 建议入口脚本执行前先设置环境变量。


def build_symbol_pairs(bars_path: Path) -> list[tuple[str, str]]:
    """从行情数据取股票池，返回 [(akshare 代码, vt_symbol), ...]。

    akshare 的 stock_zh_a_daily symbol 格式是 shXXXXXX / szXXXXXX，需要
    把仓库里的 000001.SZ 转成 sz000001，600519.SH 转成 sh600519。
    """
    df = pl.read_parquet(bars_path, columns=["vt_symbol"])
    symbols = df.get_column("vt_symbol").unique().sort().to_list()
    pairs = []
    for vt in symbols:
        code, exch = vt.split(".")
        ak_code = ("sh" if exch == "SH" else "sz") + code
        pairs.append((ak_code, vt))
    return pairs


def fetch_one(ak_code: str, vt_symbol: str, start: str, end: str, retries: int = 3):
    """拉一只股票的日频。失败重试 retries 次；仍失败返回 None。

    akshare 页面爬虫接口偶发 5xx / 连接重置，指数退避即可。字段规范化到：
    datetime / vt_symbol / close_hfq / outstanding_share / turnover_rate / amount。

    多线程里只做 http+pandas，polars 构造放到主线程做（多线程混 polars 会触发
    macOS 上偶发的段错误 crash）。这里返回一个 dict-of-lists，主线程整体转 polars。
    """
    import akshare as ak

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            df = ak.stock_zh_a_daily(symbol=ak_code, start_date=start, end_date=end, adjust="hfq")
            if df is None or len(df) == 0:
                return None
            return {
                "datetime": df["date"].astype("datetime64[ns]").tolist(),
                "vt_symbol": [vt_symbol] * len(df),
                "close_hfq": df["close"].astype(float).tolist(),
                "outstanding_share": df["outstanding_share"].astype(float).tolist(),
                "turnover_rate": df["turnover"].astype(float).tolist(),
                "amount": df["amount"].astype(float).tolist(),
            }
        except Exception as err:  # noqa: BLE001
            last_err = err
            time.sleep(2 ** attempt)
    print(f"[FAIL] {ak_code}: {last_err}")
    return None


def load_progress(progress_file: Path) -> set[str]:
    if not progress_file.exists():
        return set()
    return set(json.loads(progress_file.read_text()))


def save_progress(progress_file: Path, done: set[str]) -> None:
    progress_file.write_text(json.dumps(sorted(done)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", default="data/silver/daily_bars_raw_price.parquet")
    parser.add_argument("--start", default="20180101", help="起始日期 YYYYMMDD")
    parser.add_argument("--end", default="", help="结束日期 YYYYMMDD，缺省 = 今天")
    parser.add_argument("--workers", type=int, default=6, help="并发线程数（akshare 页面爬虫，勿开太高）")
    parser.add_argument("--out-dir", default="data/akshare", help="产物目录")
    parser.add_argument("--limit", type=int, default=0, help="仅处理前 N 只（>0，调试用）")
    args = parser.parse_args()

    if "SSL_CERT_FILE" not in os.environ:
        try:
            import certifi
            os.environ["SSL_CERT_FILE"] = certifi.where()
            print(f"[INFO] 自动设置 SSL_CERT_FILE = {os.environ['SSL_CERT_FILE']}")
        except ImportError:
            print("[WARN] certifi 未装，SSL 可能出错")

    end = args.end or time.strftime("%Y%m%d")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    shards_dir = out_dir / "_shards"
    shards_dir.mkdir(exist_ok=True)
    progress_file = out_dir / "_progress.json"
    merged_file = out_dir / "daily_bars_outstanding.parquet"

    pairs = build_symbol_pairs(Path(args.bars))
    if args.limit > 0:
        pairs = pairs[: args.limit]
    done = load_progress(progress_file)
    todo = [p for p in pairs if p[1] not in done]
    print(f"[INFO] 共 {len(pairs)} 只，已完成 {len(done)}，本次待拉 {len(todo)}")
    print(f"[INFO] 区间 {args.start} ~ {end}，并发={args.workers}")

    if not todo:
        print("[INFO] 无待拉任务，直接进入合并阶段")
    else:
        start_ts = time.time()
        batch_dicts: list[dict] = []
        batch_count = 0

        schema = {
            "datetime": pl.Datetime,
            "vt_symbol": pl.Utf8,
            "close_hfq": pl.Float64,
            "outstanding_share": pl.Float64,
            "turnover_rate": pl.Float64,
            "amount": pl.Float64,
        }

        def flush_batch() -> None:
            nonlocal batch_dicts, batch_count
            if not batch_dicts:
                return
            batch_count += 1
            # 主线程里把 dict-of-lists 合并成一张 polars 表
            merged_dict = {k: [] for k in schema}
            for d in batch_dicts:
                for k in schema:
                    merged_dict[k].extend(d[k])
            merged = pl.DataFrame(merged_dict, schema=schema)
            merged.write_parquet(shards_dir / f"shard_{batch_count:04d}.parquet")
            batch_dicts = []

        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(fetch_one, ak_code, vt_symbol, args.start, end): (ak_code, vt_symbol)
                for ak_code, vt_symbol in todo
            }
            processed = 0
            for future in as_completed(futures):
                ak_code, vt_symbol = futures[future]
                result = future.result()
                processed += 1
                if result is not None:
                    batch_dicts.append(result)
                done.add(vt_symbol)
                if processed % 50 == 0:
                    elapsed = time.time() - start_ts
                    rate = processed / elapsed
                    eta = (len(todo) - processed) / rate if rate > 0 else 0
                    print(f"[PROG] {processed}/{len(todo)} 完成 "
                          f"({rate:.1f} 只/秒，预计还剩 {eta/60:.1f} 分)")
                if len(batch_dicts) >= 200:
                    flush_batch()
                    save_progress(progress_file, done)
        flush_batch()
        save_progress(progress_file, done)
        print(f"[INFO] 抓取阶段完成，共耗时 {(time.time()-start_ts)/60:.1f} 分")

    # 合并分片 + 历史 merged（如果存在）
    print("[INFO] 合并分片 ...")
    shard_files = sorted(shards_dir.glob("shard_*.parquet"))
    if not shard_files and not merged_file.exists():
        print("[WARN] 无任何分片可合并")
        return 0

    frames: list[pl.DataFrame] = []
    if merged_file.exists():
        frames.append(pl.read_parquet(merged_file))
    for f in shard_files:
        frames.append(pl.read_parquet(f))
    if not frames:
        return 0

    merged = (
        pl.concat(frames)
        .unique(subset=["datetime", "vt_symbol"], keep="last")
        .sort(["vt_symbol", "datetime"])
    )
    merged.write_parquet(merged_file)
    print(f"[OK] 已写入 {merged_file}: {merged.height} 行，{merged.get_column('vt_symbol').unique().len()} 只")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
