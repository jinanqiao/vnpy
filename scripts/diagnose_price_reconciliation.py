"""诊断 QMT 与 AKShare 后复权价格差异。"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from vnpy.alpha.research.data_foundation.price_reconciliation import (
    diagnose_price_reconciliation,
    write_price_reconciliation_report,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="诊断 QMT/AKShare 后复权价差")
    parser.add_argument("--data-root", default="data", help="数据根目录")
    parser.add_argument("--recent-days", type=int, default=252, help="检查最近 N 个交易日")
    parser.add_argument("--price-diff-max", type=float, default=0.02, help="允许的相对价差上限")
    parser.add_argument("--coverage-min", type=float, default=0.99, help="key 覆盖率下限")
    parser.add_argument("--output-json", default="data/quality/price_reconciliation.json", help="JSON 输出路径")
    parser.add_argument("--output-md", default="data/quality/price_reconciliation.md", help="Markdown 输出路径")
    parser.add_argument("--json", action="store_true", help="打印 JSON")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    result = diagnose_price_reconciliation(
        args.data_root,
        recent_days=args.recent_days,
        price_diff_max=args.price_diff_max,
        coverage_min=args.coverage_min,
    )
    if args.output_json:
        result.write_json(args.output_json)
    if args.output_md:
        write_price_reconciliation_report(result, args.output_md)

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"价差诊断: {result.status}")
        print(f"- 覆盖率: {result.coverage}")
        print(f"- 超阈值行数: {result.diff_rows}")
        print(f"- 超阈值比例: {result.diff_ratio}")
        print(f"- 报告: {args.output_md}")
    return 0 if result.status == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
