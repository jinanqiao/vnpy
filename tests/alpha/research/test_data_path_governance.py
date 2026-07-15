from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_runtime_code_does_not_default_to_normalized_daily_bars() -> None:
    allowed = {
        ROOT / "vnpy" / "alpha" / "research" / "data_foundation" / "layout.py",
    }
    offenders: list[str] = []
    for folder in [ROOT / "scripts", ROOT / "vnpy" / "alpha" / "research"]:
        for path in folder.rglob("*.py"):
            if path in allowed or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if "data/normalized" in text or "normalized/daily_bars" in text:
                offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []
