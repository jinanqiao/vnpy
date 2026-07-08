from __future__ import annotations

import polars as pl
import plotly.graph_objects as go               # type: ignore
from plotly.subplots import make_subplots       # type: ignore


class BacktestChartRenderer:
    """Render backtest result charts from prepared dataframes."""

    def show_chart(self, daily_df: pl.DataFrame) -> None:
        fig = make_subplots(
            rows=4,
            cols=1,
            subplot_titles=["Balance", "Drawdown", "Daily Pnl", "Pnl Distribution"],
            vertical_spacing=0.06
        )

        fig.add_trace(
            go.Scatter(x=daily_df["date"], y=daily_df["balance"], mode="lines", name="Balance"),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=daily_df["date"],
                y=daily_df["drawdown"],
                fillcolor="red",
                fill="tozeroy",
                mode="lines",
                name="Drawdown",
            ),
            row=2,
            col=1,
        )
        fig.add_trace(go.Bar(y=daily_df["net_pnl"], name="Daily Pnl"), row=3, col=1)
        fig.add_trace(go.Histogram(x=daily_df["net_pnl"], nbinsx=100, name="Days"), row=4, col=1)

        fig.update_layout(height=1000, width=1000)
        fig.show()

    def show_performance(self, daily_df: pl.DataFrame, benchmark_prices: list[float]) -> None:
        performance_df: pl.DataFrame = self.calculate_performance_frame(daily_df, benchmark_prices)

        fig: go.Figure = make_subplots(
            rows=5,
            cols=1,
            subplot_titles=["Return", "Alpha", "Turnover", "Alpha Drawdown", "Alpha Drawdown with Cost"],
            vertical_spacing=0.06
        )

        fig.add_trace(
            go.Scatter(
                x=performance_df["date"],
                y=performance_df["cumulative_return"],
                mode="lines",
                name="Strategy",
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=performance_df["date"],
                y=performance_df["cumulative_return"] - performance_df["cumulative_cost"],
                mode="lines",
                name="Strategy with Cost",
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=performance_df["date"],
                y=performance_df["benchmark_return"],
                mode="lines",
                name="Benchmark",
            ),
            row=1,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=performance_df["date"],
                y=performance_df["excess_return"],
                mode="lines",
                name="Alpha",
            ),
            row=2,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=performance_df["date"],
                y=performance_df["net_excess_return"],
                mode="lines",
                name="Alpha with Cost",
            ),
            row=2,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=daily_df["date"],
                y=daily_df["turnover"] / daily_df["balance"].shift(1),
                name="Turnover",
            ),
            row=3,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=performance_df["date"],
                y=performance_df["excess_return_drawdown"],
                fill="tozeroy",
                mode="lines",
                name="Alpha Drawdown",
            ),
            row=4,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=performance_df["date"],
                y=performance_df["net_excess_return_drawdown"],
                fill="tozeroy",
                mode="lines",
                name="Alpha Drawdown with Cost",
            ),
            row=5,
            col=1,
        )

        fig.update_layout(
            height=1500,
            width=1200,
            plot_bgcolor="white",
            paper_bgcolor="white",
            xaxis=dict(showgrid=True, gridwidth=1, gridcolor="LightGray"),
            xaxis2=dict(showgrid=True, gridwidth=1, gridcolor="LightGray"),
            xaxis3=dict(showgrid=True, gridwidth=1, gridcolor="LightGray"),
            xaxis4=dict(showgrid=True, gridwidth=1, gridcolor="LightGray"),
            xaxis5=dict(showgrid=True, gridwidth=1, gridcolor="LightGray"),
            yaxis=dict(showgrid=True, gridwidth=1, gridcolor="LightGray"),
            yaxis2=dict(showgrid=True, gridwidth=1, gridcolor="LightGray"),
            yaxis3=dict(showgrid=True, gridwidth=1, gridcolor="LightGray"),
            yaxis4=dict(showgrid=True, gridwidth=1, gridcolor="LightGray"),
            yaxis5=dict(showgrid=True, gridwidth=1, gridcolor="LightGray")
        )
        fig.show()

    def calculate_performance_frame(self, daily_df: pl.DataFrame, benchmark_prices: list[float]) -> pl.DataFrame:
        return (
            daily_df.with_columns(
                cumulative_return=pl.col("balance").pct_change().cum_sum(),
                cumulative_cost=(pl.col("commission") / pl.col("balance").shift(1)).cum_sum()
            ).with_columns(
                benchmark_price=pl.Series(values=benchmark_prices, dtype=pl.Float64)
            ).with_columns(
                benchmark_return=pl.col("benchmark_price").pct_change().cum_sum()
            ).with_columns(
                excess_return=(pl.col("cumulative_return") - pl.col("benchmark_return"))
            ).with_columns(
                net_excess_return=(pl.col("excess_return") - pl.col("cumulative_cost")),
            ).with_columns(
                excess_return_drawdown=(pl.col("excess_return") - pl.col("excess_return").cum_max()),
                net_excess_return_drawdown=(pl.col("net_excess_return") - pl.col("net_excess_return").cum_max())
            )
        )
