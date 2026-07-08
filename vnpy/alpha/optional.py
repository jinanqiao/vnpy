from __future__ import annotations

from importlib import import_module
from types import ModuleType


class OptionalDependencyError(ImportError):
    """Raised when an optional alpha dependency is required but unavailable."""


def optional_dependency_message(package: str, feature: str, extra: str = "alpha") -> str:
    return (
        f"Optional dependency '{package}' is required for {feature}. "
        f"Install it directly or install vnpy with the '{extra}' optional dependency group."
    )


def require_optional_dependency(package: str, feature: str, extra: str = "alpha") -> ModuleType:
    try:
        return import_module(package)
    except ImportError as exc:
        raise OptionalDependencyError(optional_dependency_message(package, feature, extra)) from exc
