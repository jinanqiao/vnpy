from dataclasses import dataclass
from datetime import date, datetime

import polars as pl


SAMPLE_20_SYMBOLS: tuple[str, ...] = (
    "000001.SZ",
    "000002.SZ",
    "000063.SZ",
    "000333.SZ",
    "000651.SZ",
    "000858.SZ",
    "002415.SZ",
    "002475.SZ",
    "002594.SZ",
    "300059.SZ",
    "300750.SZ",
    "600000.SH",
    "600030.SH",
    "600036.SH",
    "600519.SH",
    "600887.SH",
    "601318.SH",
    "601398.SH",
    "601888.SH",
    "603259.SH",
)


@dataclass(frozen=True)
class UniverseFilterConfig:
    """Beginner-friendly stock pool filter settings."""

    min_listed_days: int = 120
    min_price: float = 2.0
    min_avg_turnover: float = 20_000_000.0
    turnover_window: int = 20


def build_sample_universe(df: pl.DataFrame, symbols: tuple[str, ...] = SAMPLE_20_SYMBOLS) -> pl.DataFrame:
    """Build a fixed sample universe for learning and unit tests."""
    _require_columns(df, {"datetime", "vt_symbol"})

    return (
        df.select(["datetime", "vt_symbol"])
        .unique()
        .sort(["datetime", "vt_symbol"])
        .with_columns(
            pl.col("vt_symbol").is_in(symbols).alias("in_universe"),
        )
    )


def apply_basic_universe_filters(
    df: pl.DataFrame,
    config: UniverseFilterConfig | None = None,
) -> pl.DataFrame:
    """
    Add tradability flags for a stock universe.

    Optional input columns:
    - `is_st`
    - `is_suspended`
    - `turnover`
    """
    config = config or UniverseFilterConfig()
    _require_columns(df, {"datetime", "vt_symbol", "close"})

    sorted_df: pl.DataFrame = df.sort(["vt_symbol", "datetime"])
    working_df: pl.DataFrame = _ensure_optional_columns(sorted_df)

    filtered: pl.DataFrame = (
        working_df
        .with_columns(
            (pl.cum_count("datetime").over("vt_symbol")).alias("listed_days"),
            pl.col("turnover")
            .rolling_mean(window_size=config.turnover_window, min_samples=1)
            .over("vt_symbol")
            .alias("avg_turnover"),
        )
        .with_columns(
            pl.col("is_st").not_().alias("not_st"),
            pl.col("is_suspended").not_().alias("not_suspended"),
            (pl.col("listed_days") >= config.min_listed_days).alias("listed_enough"),
            (pl.col("close") >= config.min_price).alias("price_ok"),
            (pl.col("avg_turnover") >= config.min_avg_turnover).alias("turnover_ok"),
        )
        .with_columns(
            (
                pl.col("not_st")
                & pl.col("not_suspended")
                & pl.col("listed_enough")
                & pl.col("price_ok")
                & pl.col("turnover_ok")
            ).alias("tradable")
        )
        .with_columns(_build_reason_expr().alias("reason"))
    )

    return filtered


def get_tradable_symbols(universe_df: pl.DataFrame, dt: date | datetime) -> list[str]:
    """Return tradable symbols for one date."""
    _require_columns(universe_df, {"datetime", "vt_symbol", "tradable"})

    symbols = (
        universe_df
        .filter((pl.col("datetime") == dt) & pl.col("tradable"))
        .sort("vt_symbol")["vt_symbol"]
        .to_list()
    )
    return [str(symbol) for symbol in symbols]


def _ensure_optional_columns(df: pl.DataFrame) -> pl.DataFrame:
    result: pl.DataFrame = df

    if "is_st" not in result.columns:
        result = result.with_columns(pl.lit(False).alias("is_st"))
    if "is_suspended" not in result.columns:
        result = result.with_columns(pl.lit(False).alias("is_suspended"))
    if "turnover" not in result.columns:
        result = result.with_columns((pl.col("close") * pl.col("volume")).alias("turnover"))

    return result


def _build_reason_expr() -> pl.Expr:
    return (
        pl.when(pl.col("not_st").not_())
        .then(pl.lit("st"))
        .when(pl.col("not_suspended").not_())
        .then(pl.lit("suspended"))
        .when(pl.col("listed_enough").not_())
        .then(pl.lit("listed_days"))
        .when(pl.col("price_ok").not_())
        .then(pl.lit("low_price"))
        .when(pl.col("turnover_ok").not_())
        .then(pl.lit("low_turnover"))
        .otherwise(pl.lit("ok"))
    )


def _require_columns(df: pl.DataFrame, columns: set[str]) -> None:
    missing: set[str] = columns - set(df.columns)
    if missing:
        missing_text: str = ", ".join(sorted(missing))
        raise ValueError(f"Missing required columns: {missing_text}")
