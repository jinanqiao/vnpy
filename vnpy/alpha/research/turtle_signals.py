from typing import Any

import polars as pl

try:
    from .contracts import validate_research_frame
except ImportError:  # pragma: no cover - allows direct file loading in tests
    from contracts import validate_research_frame  # type: ignore


def generate_turtle_signals(df: pl.DataFrame) -> pl.DataFrame:
    """
    Generate long-only turtle target positions.

    Input must contain `entry_channel` and `exit_channel`.
    `target_position` is the desired position after observing the current bar.
    Backtests should shift this column before applying returns.
    """
    _require_columns(df, {"datetime", "vt_symbol", "close", "entry_channel", "exit_channel"})
    validate_research_frame(
        df,
        required_columns={"datetime", "vt_symbol", "close", "entry_channel", "exit_channel"},
        unique_by=["datetime", "vt_symbol"],
        sorted_by=["vt_symbol", "datetime"],
        numeric_not_null=["close"],
        fail_on_unsorted=False,
    ).raise_if_failed()

    rows: list[dict[str, Any]] = []
    current_position: dict[str, int] = {}

    sorted_df: pl.DataFrame = df.sort(["vt_symbol", "datetime"])

    for row in sorted_df.iter_rows(named=True):
        symbol: str = str(row["vt_symbol"])
        position: int = current_position.get(symbol, 0)

        close: float | None = row["close"]
        entry_channel: float | None = row["entry_channel"]
        exit_channel: float | None = row["exit_channel"]

        entry_signal: bool = (
            position == 0
            and close is not None
            and entry_channel is not None
            and close > entry_channel
        )
        exit_signal: bool = (
            position == 1
            and close is not None
            and exit_channel is not None
            and close < exit_channel
        )

        if entry_signal:
            position = 1
        elif exit_signal:
            position = 0

        current_position[symbol] = position

        enriched_row: dict[str, Any] = dict(row)
        enriched_row["entry_signal"] = entry_signal
        enriched_row["exit_signal"] = exit_signal
        enriched_row["target_position"] = position
        rows.append(enriched_row)

    if not rows:
        return sorted_df.with_columns(
            pl.lit(False).alias("entry_signal"),
            pl.lit(False).alias("exit_signal"),
            pl.lit(0).alias("target_position"),
        )

    return pl.DataFrame(rows)


def _require_columns(df: pl.DataFrame, columns: set[str]) -> None:
    missing: set[str] = columns - set(df.columns)
    if missing:
        missing_text: str = ", ".join(sorted(missing))
        raise ValueError(f"Missing required columns: {missing_text}")
