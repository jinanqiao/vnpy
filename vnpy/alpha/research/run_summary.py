from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class RunSummary:
    """Structured summary for alpha research and data jobs."""

    job_name: str
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    counts: dict[str, int | float] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished_at: str | None = None

    def finish(self) -> RunSummary:
        self.finished_at = datetime.now().isoformat(timespec="seconds")
        return self

    def to_dict(self) -> dict[str, Any]:
        return _json_ready(asdict(self))

    def write_json(self, output_path: str | Path) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def _json_ready(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value
