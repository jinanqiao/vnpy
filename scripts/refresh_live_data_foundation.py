"""刷新并检查 live 数据底座。

默认只做安全诊断和 manifest 刷新；传入 --run-downloads 后才会调用已有下载脚本。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from vnpy.alpha.research.data_foundation import build_data_foundation_layout, run_data_gate
from vnpy.alpha.research.data_foundation.metadata import record_quality_check_result
from vnpy.alpha.research.data_foundation.freshness import evaluate_common_trade_date, write_freshness_report
from vnpy.alpha.research.data_foundation.price_reconciliation import (
    diagnose_price_reconciliation,
    write_price_reconciliation_report,
)
from vnpy.alpha.research.data_foundation.quarantine import quarantine_failed_source_pull
from vnpy.alpha.research.data_foundation.quarantine import apply_symbol_quarantine_to_execution_universe


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="刷新并检查 live 数据底座")
    parser.add_argument("--data-root", default="data", help="数据根目录")
    parser.add_argument("--as-of", required=True, help="评估日期 YYYY-MM-DD")
    parser.add_argument("--mode", default="live", choices=["research", "backtest", "paper", "live"])
    parser.add_argument("--run-downloads", action="store_true", help="调用已有 QMT 下载脚本刷新可拉取数据")
    parser.add_argument("--output-json", default="data/quality/live_refresh_summary.json", help="JSON 输出路径")
    parser.add_argument("--output-md", default="data/quality/live_refresh_summary.md", help="Markdown 输出路径")
    parser.add_argument("--json", action="store_true", help="打印 JSON")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    data_root = Path(args.data_root)
    command_results: list[dict[str, object]] = []
    quarantine_records: list[dict[str, object]] = []

    if args.run_downloads:
        command_results.append(_run_command([sys.executable, "scripts/build_quant_data_completeness.py", "--data-root", args.data_root], data_root, quarantine_records))
        command_results.append(_run_command([sys.executable, "scripts/download_qmt_daily_bars_full.py", "--adjust", "none", "--resume"], data_root, quarantine_records))
        command_results.append(_run_command([sys.executable, "scripts/download_qmt_daily_bars_full.py", "--adjust", "back_ratio", "--output-path", "data/silver/daily_bars_adjusted.parquet", "--shard-dir", "data/raw/qmt/daily_back_ratio", "--resume"], data_root, quarantine_records))

    downloads_ok = bool(args.run_downloads) and all(item["returncode"] == 0 for item in command_results)
    build_data_foundation_layout(args.data_root, overwrite=downloads_ok)
    freshness = evaluate_common_trade_date(args.data_root, as_of=args.as_of)
    price = diagnose_price_reconciliation(args.data_root)
    quarantine_apply_result = apply_symbol_quarantine_to_execution_universe(args.data_root)
    build_result = build_data_foundation_layout(args.data_root, overwrite=False)
    gate = run_data_gate(args.data_root, mode=args.mode, as_of=args.as_of)
    version_id = _manifest_version_id(build_result.manifest_path)
    record_quality_check_result(
        dataset_name="core_datasets",
        check_name="freshness",
        mode=args.mode,
        severity="blocking",
        status=freshness.status,
        details=freshness.to_dict(),
        version_id=version_id,
    )
    record_quality_check_result(
        dataset_name="outstanding_share_turnover",
        check_name="price_reconciliation",
        mode=args.mode,
        severity="blocking",
        status=price.status,
        details=price.to_dict(),
        version_id=version_id,
    )

    freshness_md = data_root / "quality" / "freshness_report.md"
    price_md = data_root / "quality" / "price_reconciliation.md"
    write_freshness_report(freshness, freshness_md)
    write_price_reconciliation_report(price, price_md)

    payload = {
        "status": "pass" if freshness.status == "pass" and price.status == "pass" and gate.status != "fail" else "fail",
        "as_of": args.as_of,
        "manifest_path": build_result.manifest_path,
        "manifest_version_path": build_result.manifest_version_path,
        "freshness": freshness.to_dict(),
        "price_reconciliation": price.to_dict(),
        "data_gate": gate.to_dict(),
        "symbol_quarantine": quarantine_apply_result,
        "commands": command_results,
        "quarantine_records": quarantine_records,
        "reports": {
            "freshness": str(freshness_md),
            "price_reconciliation": str(price_md),
        },
    }
    _write_outputs(payload, args.output_json, args.output_md)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"live 数据刷新检查: {payload['status']}")
        print(f"- common_trade_date: {freshness.common_trade_date}")
        print(f"- price_diff_rows: {price.diff_rows}")
        print(f"- gate: {gate.status}")
    return 0 if payload["status"] == "pass" else 2


def _run_command(command: list[str], data_root: Path, quarantine_records: list[dict[str, object]]) -> dict[str, object]:
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    item = {
        "command": command,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-2000:],
    }
    if result.returncode:
        quarantine = quarantine_failed_source_pull(
            data_root,
            source="qmt",
            dataset=Path(command[1]).stem if len(command) > 1 else "unknown",
            reason=f"command_failed_{result.returncode}",
        )
        quarantine_records.append(quarantine.to_dict())
    return item


def _write_outputs(payload: dict[str, object], json_path: str, md_path: str) -> None:
    if json_path:
        path = Path(json_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if md_path:
        lines = [
            "# Live 数据刷新检查报告",
            "",
            f"- 状态: `{payload['status']}`",
            f"- 评估日期: `{payload['as_of']}`",
            f"- manifest: `{payload['manifest_path']}`",
            f"- manifest version: `{payload['manifest_version_path']}`",
            "",
            "## 关键结果",
            "",
            f"- 新鲜度: `{payload['freshness']['status']}`",
            f"- 共同交易日: `{payload['freshness']['common_trade_date']}`",
            f"- 价差诊断: `{payload['price_reconciliation']['status']}`",
            f"- 价差超阈值行数: `{payload['price_reconciliation']['diff_rows']}`",
            f"- data gate: `{payload['data_gate']['status']}`",
            "",
        ]
        path = Path(md_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")


def _manifest_version_id(path: str) -> str | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")).get("version_id")
    except Exception:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
