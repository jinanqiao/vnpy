"""构建 PIT 过渡表。

这些表用于把公司行动候选、复权因子、涨跌停、停复牌、ST 状态先纳入
数据底座管理。当前实现只使用本地已有数据推导，不修改 QMT 凭证配置。
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from vnpy.alpha.research.data_foundation import build_data_foundation_layout, build_pit_tables


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建 PIT 过渡表")
    parser.add_argument("--data-root", default="data", help="数据根目录")
    parser.add_argument("--factor-change-threshold", type=float, default=0.001, help="复权因子变化候选阈值")
    parser.add_argument("--refresh-manifest", action="store_true", help="构建后刷新 manifest / metadata / query catalog")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出构建结果")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    result = build_pit_tables(args.data_root, factor_change_threshold=args.factor_change_threshold)
    manifest_result = None
    if args.refresh_manifest:
        manifest_result = build_data_foundation_layout(args.data_root, overwrite=False)

    if args.json:
        payload = result.to_dict()
        if manifest_result is not None:
            payload["manifest"] = manifest_result.to_dict()
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"PIT 过渡表构建完成: {args.data_root}")
        print(f"- 写出文件: {len(result.written)}")
        print(f"- 跳过文件: {len(result.skipped)}")
        print(f"- 风险提示: {len(result.warnings)}")
        print(f"- summary: {result.summary_path}")
        print(f"- report: {result.report_path}")
        if manifest_result is not None:
            print(f"- manifest: {manifest_result.manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
