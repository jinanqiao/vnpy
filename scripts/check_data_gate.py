"""运行本地数据就绪门禁。"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from vnpy.alpha.research.data_foundation.data_gate import run_data_gate, write_data_gate_report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行本地数据就绪门禁")
    parser.add_argument("--data-root", default="data", help="数据根目录")
    parser.add_argument("--mode", default="research", choices=["research", "backtest", "paper", "live"])
    parser.add_argument("--as-of", default="", help="评估日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--output-json", default="", help="JSON 报告输出路径")
    parser.add_argument("--output-md", default="", help="Markdown 报告输出路径")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    result = run_data_gate(args.data_root, mode=args.mode, as_of=args.as_of or None)
    if args.output_json:
        result.write_json(args.output_json)
    if args.output_md:
        write_data_gate_report(result, args.output_md)

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"数据门禁: {result.status} mode={result.mode}")
        print(f"- 最新行情交易日: {result.latest_trade_date}")
        print(f"- 预期交易日: {result.expected_trade_date}")
        print(f"- 阻断失败: {len(result.blocking_checks)}")
        print(f"- 警告: {len(result.warning_checks)}")
    return 0 if result.status != "fail" else 2


if __name__ == "__main__":
    raise SystemExit(main())
