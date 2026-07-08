import polars as pl

try:
    from .contracts import validate_turtle_signal_frame
except ImportError:  # pragma: no cover - allows direct file loading in tests
    from contracts import validate_turtle_signal_frame  # type: ignore


def run_long_only_backtest(
    signal_df: pl.DataFrame,
    max_positions: int = 10,
    cost_rate: float = 0.0013,
) -> pl.DataFrame:
    """
    Run a simple daily long-only backtest.

    Assumption:
    - `target_position` is known after day t close.
    - `held_position` is shifted by one day and earns day t return.
    - Turnover pays a simple proportional cost.
    """
    if max_positions <= 0:
        raise ValueError("max_positions must be positive")
    if cost_rate < 0:
        raise ValueError("cost_rate cannot be negative")

    _require_columns(signal_df, {"datetime", "vt_symbol", "close", "target_position"})
    validate_turtle_signal_frame(signal_df).raise_if_failed()

    prepared: pl.DataFrame = (
        signal_df
        .sort(["vt_symbol", "datetime"])
        .with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("vt_symbol") - 1).alias("asset_return"),
            pl.col("target_position").shift(1).fill_null(0).over("vt_symbol").alias("held_position"),
        )
    )

    weights: pl.DataFrame = _build_equal_weights(prepared, max_positions)

    weighted_returns: pl.DataFrame = (
        weights
        .with_columns(
            (pl.col("weight") * pl.col("asset_return").fill_null(0)).alias("weighted_return"),
            (pl.col("weight") - pl.col("weight").shift(1).fill_null(0).over("vt_symbol")).abs().alias("weight_change"),
        )
        .group_by("datetime")
        .agg(
            pl.col("weighted_return").sum().alias("gross_return"),
            (pl.col("weight_change").sum() / 2).alias("turnover"),
            pl.col("weight").sum().alias("gross_exposure"),
        )
        .sort("datetime")
        .with_columns(
            (pl.col("turnover") * cost_rate).alias("cost"),
        )
        .with_columns(
            (pl.col("gross_return") - pl.col("cost")).alias("net_return"),
        )
        .with_columns(
            (1 + pl.col("gross_return")).cum_prod().alias("gross_equity"),
            (1 + pl.col("net_return")).cum_prod().alias("net_equity"),
        )
    )

    return weighted_returns


def _build_equal_weights(df: pl.DataFrame, max_positions: int) -> pl.DataFrame:
    held: pl.DataFrame = df.with_columns(
        pl.when(pl.col("held_position") > 0)
        .then(1)
        .otherwise(0)
        .alias("is_held")
    )

    ranked: pl.DataFrame = held.with_columns(
        pl.col("is_held").cum_sum().over("datetime").alias("held_rank"),
        pl.col("is_held").sum().over("datetime").alias("held_count"),
    )

    return ranked.with_columns(
        pl.when((pl.col("is_held") == 1) & (pl.col("held_rank") <= max_positions))
        .then(1 / pl.min_horizontal(pl.col("held_count"), pl.lit(max_positions)))
        .otherwise(0.0)
        .alias("weight")
    )


def _require_columns(df: pl.DataFrame, columns: set[str]) -> None:
    missing: set[str] = columns - set(df.columns)
    if missing:
        missing_text: str = ", ".join(sorted(missing))
        raise ValueError(f"Missing required columns: {missing_text}")
