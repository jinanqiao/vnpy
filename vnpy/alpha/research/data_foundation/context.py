from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
import hashlib
import json

from .data_gate import DataGateMode, DataGateResult, run_data_gate


class DataContextError(RuntimeError):
    """数据上下文不可用于实盘/模拟盘入口。"""


@dataclass(frozen=True)
class DataContext:
    """当前数据版本上下文，所有实盘入口都应从 manifest 解析数据集。"""

    data_root: Path
    manifest_path: Path
    version_id: str
    latest_trade_date: str | None
    datasets: dict[str, dict[str, Any]]

    def dataset_path(self, name: str) -> Path:
        """按 manifest 中的数据集名称解析绝对路径。"""
        if name not in self.datasets:
            raise DataContextError(f"manifest 缺少数据集: {name}")
        relative_path = str(self.datasets[name].get("path") or "")
        if not relative_path:
            raise DataContextError(f"manifest 数据集缺少路径: {name}")
        return self.data_root / relative_path

    def to_dict(self) -> dict[str, Any]:
        """写入运行产物的精简数据版本摘要。"""
        return {
            "data_version_id": self.version_id,
            "data_manifest_path": str(self.manifest_path),
            "latest_trade_date": self.latest_trade_date,
            "dataset_count": len(self.datasets),
        }


CORE_DATASETS: tuple[str, ...] = (
    "daily_bars_raw_price",
    "daily_bars_adjusted",
    "trading_calendar",
    "execution_universe",
)


def load_data_context(
    data_root: str | Path = "data",
    *,
    manifest_path: str | Path | None = None,
    verify_hash: bool = False,
) -> DataContext:
    """读取当前 manifest，并校验核心数据集存在。

    verify_hash 默认关闭，因为 1400 万行行情文件逐次算哈希会拖慢实盘入口；
    刷新数据底座和验收任务需要时再显式打开。
    """
    root = Path(data_root)
    path = Path(manifest_path) if manifest_path else root / "manifest" / "data_foundation_manifest.json"
    if not path.exists():
        raise DataContextError(f"缺少数据 manifest: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    datasets = {str(item.get("name")): dict(item) for item in payload.get("datasets", [])}
    context = DataContext(
        data_root=root,
        manifest_path=path,
        version_id=str(payload.get("version_id") or ""),
        latest_trade_date=payload.get("latest_trade_date"),
        datasets=datasets,
    )

    missing = [name for name in CORE_DATASETS if name not in datasets]
    if missing:
        raise DataContextError(f"manifest 缺少核心数据集: {missing}")
    for name in CORE_DATASETS:
        dataset_path = context.dataset_path(name)
        if not dataset_path.exists():
            raise DataContextError(f"manifest 指向的核心数据不存在: {name} -> {dataset_path}")
        if "normalized/" in str(datasets[name].get("path") or ""):
            raise DataContextError(f"实盘入口禁止使用废弃 normalized 路径: {name}")
        if verify_hash:
            expected = str(datasets[name].get("sha256") or "")
            actual = _sha256(dataset_path)
            if expected and actual != expected:
                raise DataContextError(f"数据集哈希不匹配: {name}")
    return context


def validate_data_context(
    data_root: str | Path = "data",
    *,
    mode: DataGateMode | str,
    as_of: date | str | None = None,
    manifest_path: str | Path | None = None,
    verify_hash: bool = False,
) -> tuple[DataContext, DataGateResult]:
    """读取 manifest 并运行数据门禁；失败时直接抛错阻断实盘入口。"""
    context = load_data_context(data_root, manifest_path=manifest_path, verify_hash=verify_hash)
    gate = run_data_gate(data_root, mode=mode, as_of=as_of)
    if gate.status == "fail":
        failures = "; ".join(f"{item.dataset}/{item.name}: {item.detail}" for item in gate.blocking_checks)
        raise DataContextError(f"数据门禁失败，已阻断运行: {failures}")
    return context, gate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
