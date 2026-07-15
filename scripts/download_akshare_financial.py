"""T6 财务数据下载：三大报表 + 派生指标。

数据源：
    - stock_financial_report_sina(stock='sh600519', symbol='资产负债表' | '利润表' | '现金流量表')
      每张表 100+ 列，含"公告日期"字段（PIT 对齐关键）
    - stock_financial_analysis_indicator(symbol='600519', start_year='2020')
      86 个派生指标（ROE / 净利率 / 净利润同比等），无公告日（需与三表 join 打补丁）

产物：
    data/akshare/financial_reports.parquet    资产负债/利润/现金流 三表合并
    data/akshare/financial_indicators.parquet 派生指标

多进程（ProcessPoolExecutor）：akshare 内部用 py_mini_racer 做 JS 解密，
多线程会 native crash（libmini_racer 冲突），必须用多进程隔离，见
data/akshare/_download.log 与 011/013 教训。

用法:
    export SSL_CERT_FILE=$(python3 -c 'import certifi; print(certifi.where())')
    arch -arm64 /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
        scripts/download_akshare_financial.py --workers 6 [--limit 20]
"""
from __future__ import annotations

import argparse
import json
import os
import time
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import polars as pl

warnings.filterwarnings("ignore")

REPORT_TYPES = ["资产负债表", "利润表", "现金流量表"]


def build_symbol_pairs(bars_path: Path) -> list[tuple[str, str, str]]:
    """从行情数据取股票池，返回 [(sina代码, 六位数字代码, vt_symbol), ...]。

    sina 三表接口: sh600519 / sz000001
    indicator 接口: 600519 / 000001（只要六位数字）
    """
    df = pl.read_parquet(bars_path, columns=["vt_symbol"])
    symbols = df.get_column("vt_symbol").unique().sort().to_list()
    pairs = []
    for vt in symbols:
        code, exch = vt.split(".")
        sina = ("sh" if exch == "SH" else "sz") + code
        pairs.append((sina, code, vt))
    return pairs


def fetch_reports(sina_code: str, vt_symbol: str, retries: int = 2):
    """拉三张表，返回 dict-of-lists（主进程再合成 polars 表）。

    每张表带"报告日"和"公告日期"，把三张纵向拼起来，加一列 report_type 区分。
    """
    import akshare as ak

    rows: list[dict] = []
    for report_type in REPORT_TYPES:
        last_err: Exception | None = None
        df = None
        for attempt in range(retries):
            try:
                df = ak.stock_financial_report_sina(stock=sina_code, symbol=report_type)
                if df is None or len(df) == 0:
                    df = None
                break
            except Exception as err:  # noqa: BLE001
                last_err = err
                time.sleep(2 ** attempt)
        if df is None:
            continue
        # 三表列数不一，字段各异 —— 只留 (报告日 + 公告日期 + JSON payload) 三列
        # 保持一致 schema，检验时再按 report_type 展开
        payload_cols = [c for c in df.columns if c not in ("报告日", "公告日期", "更新日期")]
        for _, r in df.iterrows():
            rows.append({
                "vt_symbol": vt_symbol,
                "report_type": report_type,
                "report_date": str(r.get("报告日", "")),
                "announce_date": str(r.get("公告日期", "")),
                "payload_json": json.dumps({c: (None if pd_isna(r[c]) else str(r[c])) for c in payload_cols}, ensure_ascii=False),
            })
    return rows


def fetch_indicators(numeric_code: str, vt_symbol: str, start_year: str = "2017", retries: int = 2):
    """拉派生指标（86 列），返回 dict-of-lists。"""
    import akshare as ak

    last_err: Exception | None = None
    df = None
    for attempt in range(retries):
        try:
            df = ak.stock_financial_analysis_indicator(symbol=numeric_code, start_year=start_year)
            if df is None or len(df) == 0:
                return []
            break
        except Exception as err:  # noqa: BLE001
            last_err = err
            time.sleep(2 ** attempt)
    if df is None:
        return []

    payload_cols = [c for c in df.columns if c != "日期"]
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "vt_symbol": vt_symbol,
            "report_date": str(r.get("日期", "")),
            "payload_json": json.dumps({c: (None if pd_isna(r[c]) else str(r[c])) for c in payload_cols}, ensure_ascii=False),
        })
    return rows


def pd_isna(v) -> bool:  # noqa: ANN001
    """避免直接 import pandas 在 worker 里重复触发 mini-racer；用简单判断。"""
    try:
        import math
        if v is None:
            return True
        if isinstance(v, float) and math.isnan(v):
            return True
        return False
    except Exception:  # noqa: BLE001
        return False


def worker_reports(args: tuple):
    sina, _, vt = args
    return fetch_reports(sina, vt)


def worker_indicators(args: tuple):
    _, code, vt = args
    return fetch_indicators(code, vt)


def load_progress(progress_file: Path) -> set[str]:
    if not progress_file.exists():
        return set()
    return set(json.loads(progress_file.read_text()))


def save_progress(progress_file: Path, done: set[str]) -> None:
    progress_file.write_text(json.dumps(sorted(done)))


def run_stage(
    pairs: list,
    stage_name: str,
    worker_fn,
    out_dir: Path,
    workers: int,
    schema: dict,
) -> None:
    """通用阶段跑法：多进程拉数据 → 每 200 只落盘一次分片 → 最后合并。"""
    shards_dir = out_dir / f"_shards_{stage_name}"
    shards_dir.mkdir(exist_ok=True)
    progress_file = out_dir / f"_progress_{stage_name}.json"
    merged_file = out_dir / f"financial_{stage_name}.parquet"

    done = load_progress(progress_file)
    todo = [p for p in pairs if p[2] not in done]
    print(f"[INFO/{stage_name}] 共 {len(pairs)} 只，已完成 {len(done)}，本次待拉 {len(todo)}")

    if todo:
        start_ts = time.time()
        batch_rows: list[dict] = []
        batch_count = 0

        def flush() -> None:
            nonlocal batch_rows, batch_count
            if not batch_rows:
                return
            batch_count += 1
            merged = {k: [r.get(k) for r in batch_rows] for k in schema}
            df = pl.DataFrame(merged, schema=schema)
            df.write_parquet(shards_dir / f"shard_{batch_count:04d}.parquet")
            batch_rows = []

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(worker_fn, p): p for p in todo}
            processed = 0
            for future in as_completed(futures):
                p = futures[future]
                _, _, vt = p
                try:
                    rows = future.result()
                    if rows:
                        batch_rows.extend(rows)
                except Exception as err:  # noqa: BLE001
                    print(f"[FAIL/{stage_name}] {vt}: {err}")
                done.add(vt)
                processed += 1
                if processed % 100 == 0:
                    elapsed = time.time() - start_ts
                    rate = processed / elapsed
                    eta = (len(todo) - processed) / rate if rate > 0 else 0
                    print(f"[PROG/{stage_name}] {processed}/{len(todo)} "
                          f"({rate:.2f} 只/秒，ETA {eta/60:.1f} 分)", flush=True)
                if len(batch_rows) >= 1000:
                    flush()
                    save_progress(progress_file, done)
        flush()
        save_progress(progress_file, done)
        print(f"[OK/{stage_name}] 抓取阶段耗时 {(time.time()-start_ts)/60:.1f} 分")

    # 合并分片
    print(f"[INFO/{stage_name}] 合并分片 ...")
    shard_files = sorted(shards_dir.glob("shard_*.parquet"))
    if not shard_files and not merged_file.exists():
        print(f"[WARN/{stage_name}] 无分片可合并")
        return
    frames = []
    if merged_file.exists():
        frames.append(pl.read_parquet(merged_file))
    for f in shard_files:
        frames.append(pl.read_parquet(f))
    merged = (
        pl.concat(frames)
        .unique(subset=list(schema.keys())[:3], keep="last")  # 前 3 列做唯一键
        .sort(list(schema.keys())[:3])
    )
    merged.write_parquet(merged_file)
    print(f"[OK/{stage_name}] {merged_file}: {merged.height} 行, "
          f"{merged.get_column('vt_symbol').unique().len()} 只")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", default="data/silver/daily_bars_raw_price.parquet")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--out-dir", default="data/akshare")
    parser.add_argument("--limit", type=int, default=0, help="仅前 N 只（调试）")
    parser.add_argument(
        "--stage", default="both", choices=["both", "reports", "indicators"],
        help="both = 先拉三表再拉指标；reports = 只拉三表；indicators = 只拉指标",
    )
    args = parser.parse_args()

    if "SSL_CERT_FILE" not in os.environ:
        try:
            import certifi
            os.environ["SSL_CERT_FILE"] = certifi.where()
            print(f"[INFO] 自动设置 SSL_CERT_FILE = {os.environ['SSL_CERT_FILE']}")
        except ImportError:
            print("[WARN] certifi 未装")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pairs = build_symbol_pairs(Path(args.bars))
    if args.limit > 0:
        pairs = pairs[: args.limit]

    reports_schema = {
        "vt_symbol": pl.Utf8,
        "report_type": pl.Utf8,
        "report_date": pl.Utf8,
        "announce_date": pl.Utf8,
        "payload_json": pl.Utf8,
    }
    indicators_schema = {
        "vt_symbol": pl.Utf8,
        "report_date": pl.Utf8,
        "payload_json": pl.Utf8,
    }

    if args.stage in ("both", "reports"):
        run_stage(pairs, "reports", worker_reports, out_dir, args.workers, reports_schema)

    if args.stage in ("both", "indicators"):
        run_stage(pairs, "indicators", worker_indicators, out_dir, args.workers, indicators_schema)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
