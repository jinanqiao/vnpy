"""构建本地分层数据底座。

该脚本只做增量复制：保留旧目录不动，把现有数据映射到 raw/bronze/silver/gold
新结构，并生成 manifest 与人读报告。
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from vnpy.alpha.research.data_foundation import build_data_foundation_layout


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建本地分层数据底座")
    parser.add_argument("--data-root", default="data", help="数据根目录")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的新分层目标文件")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出构建结果")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    result = build_data_foundation_layout(args.data_root, overwrite=args.overwrite)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"数据底座构建完成: {args.data_root}")
        print(f"- 新复制文件: {len(result.copied)}")
        print(f"- 已存在跳过: {len(result.skipped_existing)}")
        print(f"- 缺失源文件: {len(result.missing_sources)}")
        print(f"- manifest: {result.manifest_path}")
        print(f"- report: {result.report_path}")
        print(f"- metadata: {result.metadata_path}")
        print(f"- query catalog: {result.query_catalog_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
