"""Alpha package public exports.

The package keeps imports lazy so lightweight submodules can be used without
loading optional modeling, GUI, or backtesting dependencies up front.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any


_EXPORTS: dict[str, tuple[str, str]] = {
    "logger": ("vnpy.alpha.logger", "logger"),
    "AlphaDataset": ("vnpy.alpha.dataset", "AlphaDataset"),
    "Segment": ("vnpy.alpha.dataset", "Segment"),
    "to_datetime": ("vnpy.alpha.dataset", "to_datetime"),
    "register_functions": ("vnpy.alpha.dataset", "register_functions"),
    "AlphaModel": ("vnpy.alpha.model", "AlphaModel"),
    "AlphaStrategy": ("vnpy.alpha.strategy", "AlphaStrategy"),
    "BacktestingEngine": ("vnpy.alpha.strategy", "BacktestingEngine"),
    "AlphaLab": ("vnpy.alpha.lab", "AlphaLab"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
