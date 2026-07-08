import polars as pl

try:
    from .contracts import validate_turtle_price_frame
except ImportError:  # pragma: no cover - allows direct file loading in tests
    from contracts import validate_turtle_price_frame  # type: ignore


def calculate_donchian_channels(
    df: pl.DataFrame,
    entry_window: int = 20,
    exit_window: int = 10,
) -> pl.DataFrame:
    """
    Add Donchian entry and exit channels.

    The channels are shifted by one bar, so today's signal only sees prices
    that were already known before today. This is the key anti-lookahead rule.
    """
    _validate_window("entry_window", entry_window)
    _validate_window("exit_window", exit_window)
    _require_columns(df, {"datetime", "vt_symbol", "high", "low"})
    validate_turtle_price_frame(df).raise_if_failed()

    sorted_df: pl.DataFrame = df.sort(["vt_symbol", "datetime"])

    return sorted_df.with_columns(
        pl.col("high")
        .rolling_max(window_size=entry_window, min_samples=entry_window)
        .shift(1)
        .over("vt_symbol")
        .alias("entry_channel"),
        pl.col("low")
        .rolling_min(window_size=exit_window, min_samples=exit_window)
        .shift(1)
        .over("vt_symbol")
        .alias("exit_channel"),
    )


def calculate_atr(df: pl.DataFrame, atr_window: int = 20) -> pl.DataFrame:
    """
    Add true range and ATR columns.

    True range is the largest of:
    - high - low
    - abs(high - previous close)
    - abs(low - previous close)
    """
    _validate_window("atr_window", atr_window)
    _require_columns(df, {"datetime", "vt_symbol", "high", "low", "close"})
    validate_turtle_price_frame(df).raise_if_failed()

    sorted_df: pl.DataFrame = df.sort(["vt_symbol", "datetime"])

    df_with_pre_close: pl.DataFrame = sorted_df.with_columns(
        pl.col("close").shift(1).over("vt_symbol").alias("pre_close")
    )

    high_low = pl.col("high") - pl.col("low")
    high_pre_close = (pl.col("high") - pl.col("pre_close")).abs().fill_null(high_low)
    low_pre_close = (pl.col("low") - pl.col("pre_close")).abs().fill_null(high_low)

    return (
        df_with_pre_close
        .with_columns(
            pl.max_horizontal(high_low, high_pre_close, low_pre_close).alias("true_range")
        )
        .with_columns(
            pl.col("true_range")
            .rolling_mean(window_size=atr_window, min_samples=atr_window)
            .over("vt_symbol")
            .alias("atr")
        )
    )


def add_turtle_indicators(
    df: pl.DataFrame,
    entry_window: int = 20,
    exit_window: int = 10,
    atr_window: int = 20,
) -> pl.DataFrame:
    """Add Donchian channels and ATR in one beginner-friendly call."""
    channel_df: pl.DataFrame = calculate_donchian_channels(df, entry_window, exit_window)
    return calculate_atr(channel_df, atr_window)


def _require_columns(df: pl.DataFrame, columns: set[str]) -> None:
    missing: set[str] = columns - set(df.columns)
    if missing:
        missing_text: str = ", ".join(sorted(missing))
        raise ValueError(f"Missing required columns: {missing_text}")


def _validate_window(name: str, value: int) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")
