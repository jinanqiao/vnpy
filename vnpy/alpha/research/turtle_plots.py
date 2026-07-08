from pathlib import Path
from typing import Any

import polars as pl


def save_equity_chart(daily_df: pl.DataFrame, output_path: str | Path) -> Path:
    """Save strategy equity chart as an HTML file."""
    _require_columns(daily_df, {"datetime", "gross_equity", "net_equity"})
    go = _load_plotly()

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=_series_values(daily_df, "datetime"), y=_series_values(daily_df, "gross_equity"), name="Gross Equity"))
    fig.add_trace(go.Scatter(x=_series_values(daily_df, "datetime"), y=_series_values(daily_df, "net_equity"), name="Net Equity"))
    fig.update_layout(title="Turtle Strategy Equity", xaxis_title="Date", yaxis_title="Equity")

    return _write_html(fig, output_path)


def save_drawdown_chart(
    daily_df: pl.DataFrame,
    output_path: str | Path,
    equity_column: str = "net_equity",
) -> Path:
    """Save drawdown chart as an HTML file."""
    _require_columns(daily_df, {"datetime", equity_column})
    go = _load_plotly()

    chart_df: pl.DataFrame = daily_df.with_columns(
        (pl.col(equity_column) / pl.col(equity_column).cum_max() - 1).alias("drawdown")
    )

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(x=_series_values(chart_df, "datetime"), y=_series_values(chart_df, "drawdown"), name="Drawdown", fill="tozeroy")
    )
    fig.update_layout(title="Turtle Strategy Drawdown", xaxis_title="Date", yaxis_title="Drawdown")

    return _write_html(fig, output_path)


def save_signal_chart(
    signal_df: pl.DataFrame,
    vt_symbol: str,
    output_path: str | Path,
) -> Path:
    """Save close price, channels, and entry/exit points for one symbol."""
    _require_columns(
        signal_df,
        {"datetime", "vt_symbol", "close", "entry_channel", "exit_channel", "entry_signal", "exit_signal"},
    )
    go = _load_plotly()

    chart_df: pl.DataFrame = signal_df.filter(pl.col("vt_symbol") == vt_symbol).sort("datetime")
    entries: pl.DataFrame = chart_df.filter(pl.col("entry_signal"))
    exits: pl.DataFrame = chart_df.filter(pl.col("exit_signal"))

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=_series_values(chart_df, "datetime"), y=_series_values(chart_df, "close"), name="Close"))
    fig.add_trace(
        go.Scatter(x=_series_values(chart_df, "datetime"), y=_series_values(chart_df, "entry_channel"), name="Entry Channel")
    )
    fig.add_trace(go.Scatter(x=_series_values(chart_df, "datetime"), y=_series_values(chart_df, "exit_channel"), name="Exit Channel"))
    fig.add_trace(
        go.Scatter(
            x=_series_values(entries, "datetime"),
            y=_series_values(entries, "close"),
            mode="markers",
            marker={"symbol": "triangle-up", "size": 10},
            name="Entry",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=_series_values(exits, "datetime"),
            y=_series_values(exits, "close"),
            mode="markers",
            marker={"symbol": "triangle-down", "size": 10},
            name="Exit",
        )
    )
    fig.update_layout(title=f"Turtle Signals: {vt_symbol}", xaxis_title="Date", yaxis_title="Price")

    return _write_html(fig, output_path)


def _load_plotly() -> Any:
    try:
        import plotly.graph_objects as go  # type: ignore
    except ImportError as exc:
        raise ImportError("plotly is required to save turtle strategy charts") from exc

    return go


def _write_html(fig: Any, output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(path)
    return path


def _series_values(df: pl.DataFrame, column: str) -> list[Any]:
    values: list[Any] = df[column].to_list()
    return values


def _require_columns(df: pl.DataFrame, columns: set[str]) -> None:
    missing: set[str] = columns - set(df.columns)
    if missing:
        missing_text: str = ", ".join(sorted(missing))
        raise ValueError(f"Missing required columns: {missing_text}")
