from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import json

import polars as pl


@dataclass(frozen=True)
class PitUniverseConfig:
    """Settings for building a practical point-in-time universe from local data."""

    min_listed_days: int = 120
    min_price: float = 2.0
    min_avg_turnover: float = 20_000_000.0
    turnover_window: int = 20
    max_buy_turnover_ratio: float = 0.10
    max_sell_turnover_ratio: float = 0.10


@dataclass(frozen=True)
class PitUniverseSummary:
    """Build summary for PIT universe outputs."""

    created_at: str
    start_date: str | None
    end_date: str | None
    master_symbol_count: int
    bar_symbol_count: int
    daily_universe_rows: int
    daily_status_rows: int
    research_rows: int
    tradable_rows: int
    execution_rows: int
    limitations: list[str]


def build_daily_universe(
    symbols_df: pl.DataFrame,
    trading_dates_df: pl.DataFrame,
    daily_bars: pl.DataFrame,
) -> pl.DataFrame:
    """Build daily stock existence flags using list/delist dates and first available bar."""
    _require_columns(symbols_df, {"vt_symbol", "name", "board", "list_date", "delist_date"})
    _require_columns(trading_dates_df, {"trade_date"})
    _require_columns(daily_bars, {"datetime", "vt_symbol"})

    calendar = _normalize_calendar(trading_dates_df, daily_bars)
    master = _prepare_symbol_master(symbols_df, daily_bars)

    return (
        calendar
        .join(master, how="cross")
        .with_columns(
            (
                (pl.col("datetime") >= pl.col("effective_list_date"))
                & (
                    pl.col("effective_delist_date").is_null()
                    | (pl.col("datetime") <= pl.col("effective_delist_date"))
                )
            ).alias("in_universe")
        )
        .filter(pl.col("in_universe"))
        .select(
            [
                "datetime",
                "vt_symbol",
                "name",
                "board",
                "exchange",
                "list_date",
                "delist_date",
                "effective_list_date",
                "effective_delist_date",
                "in_universe",
            ]
        )
        .sort(["datetime", "vt_symbol"])
    )


def build_daily_status(
    daily_universe: pl.DataFrame,
    daily_bars: pl.DataFrame,
    config: PitUniverseConfig | None = None,
) -> pl.DataFrame:
    """Build daily status and liquidity fields for PIT universe filtering."""
    config = config or PitUniverseConfig()
    _require_columns(daily_universe, {"datetime", "vt_symbol", "name", "board", "in_universe"})
    _require_columns(daily_bars, {"datetime", "vt_symbol", "open", "high", "low", "close", "volume", "turnover"})

    bars = daily_bars.sort(["vt_symbol", "datetime"]).with_columns(
        pl.cum_count("datetime").over("vt_symbol").alias("bar_listed_days"),
        pl.col("turnover")
        .rolling_mean(window_size=config.turnover_window, min_samples=1)
        .over("vt_symbol")
        .alias("avg_turnover_20"),
    )

    return (
        daily_universe
        .join(bars, on=["datetime", "vt_symbol"], how="left")
        .with_columns(
            pl.col("name").str.contains("ST").fill_null(False).alias("is_st"),
            (pl.col("volume").fill_null(0) <= 0).alias("is_zero_volume"),
            (pl.col("turnover").fill_null(0) <= 0).alias("is_zero_turnover"),
            pl.col("close").is_null().alias("missing_bar"),
            pl.col("bar_listed_days").fill_null(0).cast(pl.Int64).alias("listed_days"),
        )
        .with_columns(
            (pl.col("is_zero_volume") | pl.col("is_zero_turnover") | pl.col("missing_bar")).alias("is_suspended"),
            (pl.col("close") >= config.min_price).fill_null(False).alias("price_ok"),
            (pl.col("avg_turnover_20") >= config.min_avg_turnover).fill_null(False).alias("liquidity_ok"),
            (pl.col("listed_days") >= config.min_listed_days).alias("listed_enough"),
        )
        .with_columns(
            (
                pl.col("in_universe")
                & pl.col("is_st").not_()
                & pl.col("is_suspended").not_()
                & pl.col("listed_enough")
                & pl.col("price_ok")
                & pl.col("liquidity_ok")
            ).alias("tradable")
        )
        .with_columns(_status_reason_expr().alias("status_reason"))
        .sort(["datetime", "vt_symbol"])
    )


def build_universe_layers(
    daily_status: pl.DataFrame,
    config: PitUniverseConfig | None = None,
) -> dict[str, pl.DataFrame]:
    """Build research, tradable, and execution universe layers."""
    config = config or PitUniverseConfig()
    _require_columns(daily_status, {"datetime", "vt_symbol", "in_universe", "tradable", "turnover"})

    research = (
        daily_status
        .with_columns(
            pl.col("in_universe").alias("in_research"),
            pl.when(pl.col("in_universe")).then(pl.lit("ok")).otherwise(pl.lit("not_listed")).alias("research_reason"),
        )
        .filter(pl.col("in_research"))
        .select(["datetime", "vt_symbol", "in_research", "research_reason"])
    )

    tradable = (
        daily_status
        .with_columns(
            pl.col("tradable").alias("in_tradable"),
            pl.col("status_reason").alias("tradable_reason"),
        )
        .filter(pl.col("in_research") if "in_research" in daily_status.columns else pl.col("in_universe"))
        .select(["datetime", "vt_symbol", "in_tradable", "tradable_reason"])
    )

    execution = (
        daily_status
        .with_columns(
            (
                pl.col("tradable")
                & (pl.col("turnover").fill_null(0) * config.max_buy_turnover_ratio > 0)
                & (pl.col("turnover").fill_null(0) * config.max_sell_turnover_ratio > 0)
            ).alias("in_execution")
        )
        .with_columns(
            pl.when(pl.col("in_execution"))
            .then(pl.lit("ok"))
            .otherwise(pl.col("status_reason"))
            .alias("execution_reason")
        )
        .filter(pl.col("in_universe"))
        .select(["datetime", "vt_symbol", "in_execution", "execution_reason"])
    )

    return {
        "research_universe": research.sort(["datetime", "vt_symbol"]),
        "tradable_universe": tradable.sort(["datetime", "vt_symbol"]),
        "execution_universe": execution.sort(["datetime", "vt_symbol"]),
    }


def build_pit_universe(
    symbols_df: pl.DataFrame,
    trading_dates_df: pl.DataFrame,
    daily_bars: pl.DataFrame,
    output_dir: str | Path,
    config: PitUniverseConfig | None = None,
) -> PitUniverseSummary:
    """Build and save all PIT universe artifacts."""
    config = config or PitUniverseConfig()
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    daily_universe = build_daily_universe(symbols_df, trading_dates_df, daily_bars)
    daily_status = build_daily_status(daily_universe, daily_bars, config)
    layers = build_universe_layers(daily_status, config)

    daily_universe.write_parquet(output_path / "daily_universe.parquet")
    daily_status.write_parquet(output_path / "daily_status.parquet")
    for name, frame in layers.items():
        frame.write_parquet(output_path / f"{name}.parquet")

    summary = _build_summary(symbols_df, daily_bars, daily_universe, daily_status, layers)
    (output_path / "universe_manifest.json").write_text(
        json.dumps({**asdict(summary), "config": asdict(config)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_verification(output_path, summary, daily_status, layers)
    return summary


def _prepare_symbol_master(symbols_df: pl.DataFrame, daily_bars: pl.DataFrame) -> pl.DataFrame:
    first_bars = (
        daily_bars
        .group_by("vt_symbol")
        .agg(pl.col("datetime").min().alias("first_bar_date"))
    )

    return (
        symbols_df
        .select(["vt_symbol", "name", "board", "exchange", "list_date", "delist_date"])
        .join(first_bars, on="vt_symbol", how="left")
        .with_columns(
            _parse_qmt_date_expr("list_date").alias("parsed_list_date"),
            _parse_qmt_date_expr("delist_date").alias("parsed_delist_date"),
        )
        .with_columns(
            pl.coalesce(["parsed_list_date", "first_bar_date"]).alias("effective_list_date"),
            pl.when(pl.col("parsed_delist_date") > datetime(2090, 1, 1))
            .then(None)
            .otherwise(pl.col("parsed_delist_date"))
            .alias("effective_delist_date"),
        )
        .filter(pl.col("effective_list_date").is_not_null())
    )


def _normalize_calendar(trading_dates_df: pl.DataFrame, daily_bars: pl.DataFrame) -> pl.DataFrame:
    date_range = daily_bars.select(
        pl.col("datetime").min().alias("start"),
        pl.col("datetime").max().alias("end"),
    ).row(0, named=True)

    return (
        trading_dates_df
        .select(pl.col("trade_date").cast(pl.Datetime).alias("datetime"))
        .unique()
        .filter((pl.col("datetime") >= date_range["start"]) & (pl.col("datetime") <= date_range["end"]))
        .sort("datetime")
    )


def _parse_qmt_date_expr(column: str) -> pl.Expr:
    cleaned = (
        pl.when(pl.col(column).is_null())
        .then(None)
        .when(pl.col(column).cast(pl.String).is_in(["", "0", "00000000"]))
        .then(None)
        .otherwise(pl.col(column).cast(pl.String))
    )
    return cleaned.str.strptime(pl.Datetime, format="%Y%m%d", strict=False)


def _status_reason_expr() -> pl.Expr:
    return (
        pl.when(pl.col("in_universe").not_())
        .then(pl.lit("not_in_universe"))
        .when(pl.col("is_st"))
        .then(pl.lit("st"))
        .when(pl.col("is_suspended"))
        .then(pl.lit("suspended_or_missing_bar"))
        .when(pl.col("listed_enough").not_())
        .then(pl.lit("listed_days"))
        .when(pl.col("price_ok").not_())
        .then(pl.lit("low_price"))
        .when(pl.col("liquidity_ok").not_())
        .then(pl.lit("low_turnover"))
        .otherwise(pl.lit("ok"))
    )


def _build_summary(
    symbols_df: pl.DataFrame,
    daily_bars: pl.DataFrame,
    daily_universe: pl.DataFrame,
    daily_status: pl.DataFrame,
    layers: dict[str, pl.DataFrame],
) -> PitUniverseSummary:
    date_range = daily_universe.select(
        pl.col("datetime").min().alias("start"),
        pl.col("datetime").max().alias("end"),
    ).row(0, named=True)

    return PitUniverseSummary(
        created_at=datetime.now().isoformat(timespec="seconds"),
        start_date=str(date_range["start"]) if date_range["start"] else None,
        end_date=str(date_range["end"]) if date_range["end"] else None,
        master_symbol_count=symbols_df.height,
        bar_symbol_count=int(daily_bars.select(pl.col("vt_symbol").n_unique()).item()),
        daily_universe_rows=daily_universe.height,
        daily_status_rows=daily_status.height,
        research_rows=layers["research_universe"].height,
        tradable_rows=layers["tradable_universe"].filter(pl.col("in_tradable")).height,
        execution_rows=layers["execution_universe"].filter(pl.col("in_execution")).height,
        limitations=[
            "Uses current QMT master data plus local bar history; not a vendor-grade historical PIT master.",
            "Historical ST is approximated from current security names.",
            "Suspension is inferred from zero volume/turnover or missing bars.",
            "Historical limit-up/down and corporate action PIT tables are not yet available.",
        ],
    )


def _write_verification(
    output_path: Path,
    summary: PitUniverseSummary,
    daily_status: pl.DataFrame,
    layers: dict[str, pl.DataFrame],
) -> None:
    reason_counts = daily_status.group_by("status_reason").len().sort("status_reason").to_dicts()
    lines = [
        "# PIT Universe Verification",
        "",
        f"- Start date: `{summary.start_date}`",
        f"- End date: `{summary.end_date}`",
        f"- Master symbols: `{summary.master_symbol_count}`",
        f"- Symbols with bars: `{summary.bar_symbol_count}`",
        f"- Daily universe rows: `{summary.daily_universe_rows}`",
        f"- Daily status rows: `{summary.daily_status_rows}`",
        f"- Research rows: `{summary.research_rows}`",
        f"- Tradable true rows: `{summary.tradable_rows}`",
        f"- Execution true rows: `{summary.execution_rows}`",
        "",
        "## Status Reason Counts",
        "",
        json.dumps(reason_counts, ensure_ascii=False, indent=2),
        "",
        "## Layer Files",
        "",
    ]
    for name in ["research_universe", "tradable_universe", "execution_universe"]:
        frame = layers[name]
        lines.append(f"- `{name}.parquet`: rows={frame.height}")
    lines.extend(["", "## Known Limitations", ""])
    lines.extend([f"- {item}" for item in summary.limitations])
    (output_path / "universe_verification.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _require_columns(df: pl.DataFrame, columns: set[str]) -> None:
    missing = columns - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")
