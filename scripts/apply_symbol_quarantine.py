"""Apply symbol-level quarantine lists to the execution universe."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from vnpy.alpha.research.data_foundation.quarantine import apply_symbol_quarantine_to_execution_universe


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply quarantined symbols to gold/execution_universe.parquet")
    parser.add_argument("--data-root", default="data", help="数据根目录")
    parser.add_argument("--json", action="store_true", help="打印 JSON 结果")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = apply_symbol_quarantine_to_execution_universe(args.data_root)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("symbol quarantine applied")
        print(f"- quarantined_symbol_count: {result['quarantined_symbol_count']}")
        print(f"- removed_rows: {result['removed_rows']}")
        print(f"- removed_symbols: {result['removed_symbols']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
