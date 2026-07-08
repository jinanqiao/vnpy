from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl


@dataclass(frozen=True)
class ResearchValidationResult:
    """Validation result for research dataframes."""

    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)

    def raise_if_failed(self) -> None:
        if self.passed:
            return
        raise ValueError("; ".join(self.errors))


def validate_research_frame(
    df: pl.DataFrame,
    *,
    required_columns: set[str] | None = None,
    unique_by: list[str] | None = None,
    sorted_by: list[str] | None = None,
    numeric_not_null: list[str] | None = None,
    assumptions: list[str] | None = None,
    fail_on_unsorted: bool = True,
) -> ResearchValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    required_columns = required_columns or set()

    missing = required_columns - set(df.columns)
    if missing:
        errors.append(f"Missing required columns: {', '.join(sorted(missing))}")

    if unique_by and set(unique_by).issubset(df.columns):
        duplicate_count = df.group_by(unique_by).len().filter(pl.col("len") > 1).height
        if duplicate_count:
            errors.append(f"Duplicate keys found for {', '.join(unique_by)}: {duplicate_count}")

    if sorted_by and set(sorted_by).issubset(df.columns):
        sorted_df = df.sort(sorted_by)
        if not df.select(sorted_by).equals(sorted_df.select(sorted_by)):
            message = f"Frame is not sorted by {', '.join(sorted_by)}"
            if fail_on_unsorted:
                errors.append(message)
            else:
                warnings.append(message)

    for column in numeric_not_null or []:
        if column not in df.columns:
            continue
        null_count = df.filter(pl.col(column).is_null()).height
        if null_count:
            errors.append(f"Column {column} has {null_count} null values")

    return ResearchValidationResult(
        passed=not errors,
        errors=errors,
        warnings=warnings,
        assumptions=assumptions or [],
    )


def validate_turtle_price_frame(df: pl.DataFrame, *, fail_on_unsorted: bool = False) -> ResearchValidationResult:
    return validate_research_frame(
        df,
        required_columns={"datetime", "vt_symbol", "open", "high", "low", "close"},
        unique_by=["datetime", "vt_symbol"],
        sorted_by=["vt_symbol", "datetime"],
        numeric_not_null=["open", "high", "low", "close"],
        assumptions=["Input price frame is treated as point-in-time daily OHLCV data."],
        fail_on_unsorted=fail_on_unsorted,
    )


def validate_turtle_signal_frame(df: pl.DataFrame, *, fail_on_unsorted: bool = False) -> ResearchValidationResult:
    return validate_research_frame(
        df,
        required_columns={"datetime", "vt_symbol", "close", "target_position"},
        unique_by=["datetime", "vt_symbol"],
        sorted_by=["vt_symbol", "datetime"],
        numeric_not_null=["close", "target_position"],
        assumptions=["Backtest applies a one-bar shift to target_position before earning returns."],
        fail_on_unsorted=fail_on_unsorted,
    )
