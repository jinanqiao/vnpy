from collections import defaultdict
from datetime import date

import polars as pl

from vnpy.trader.constant import Direction
from vnpy.trader.object import TradeData


class ContractDailyResult:
    """Contract daily profit and loss result."""

    def __init__(self, result_date: date, close_price: float) -> None:
        """Constructor."""
        self.date: date = result_date
        self.close_price: float = close_price
        self.pre_close: float = 0

        self.trades: list[TradeData] = []
        self.trade_count: int = 0

        self.start_pos: float = 0
        self.end_pos: float = 0

        self.turnover: float = 0
        self.commission: float = 0

        self.trading_pnl: float = 0
        self.holding_pnl: float = 0
        self.total_pnl: float = 0
        self.net_pnl: float = 0

    def add_trade(self, trade: TradeData) -> None:
        """Add trade information."""
        self.trades.append(trade)

    def calculate_pnl(
        self,
        pre_close: float,
        start_pos: float,
        size: float,
        long_rate: float,
        short_rate: float,
    ) -> None:
        """Calculate profit and loss."""
        if pre_close:
            self.pre_close = pre_close

        self.start_pos = start_pos
        self.end_pos = start_pos

        self.holding_pnl = self.start_pos * (self.close_price - self.pre_close) * size

        self.trade_count = len(self.trades)

        for trade in self.trades:
            if trade.direction == Direction.LONG:
                pos_change: float = trade.volume
                rate: float = long_rate
            else:
                pos_change = -trade.volume
                rate = short_rate

            self.end_pos += pos_change

            turnover: float = trade.volume * size * trade.price

            self.trading_pnl += pos_change * (self.close_price - trade.price) * size
            self.turnover += turnover
            self.commission += turnover * rate

        self.total_pnl = self.trading_pnl + self.holding_pnl
        self.net_pnl = self.total_pnl - self.commission

    def update_close_price(self, close_price: float) -> None:
        """Update daily close price."""
        self.close_price = close_price


class PortfolioDailyResult:
    """Portfolio daily profit and loss result."""

    def __init__(self, result_date: date, close_prices: dict[str, float]) -> None:
        """Constructor."""
        self.date: date = result_date
        self.close_prices: dict[str, float] = close_prices
        self.pre_closes: dict[str, float] = {}
        self.start_poses: dict[str, float] = {}
        self.end_poses: dict[str, float] = {}

        self.contract_results: dict[str, ContractDailyResult] = {}

        for vt_symbol, close_price in close_prices.items():
            self.contract_results[vt_symbol] = ContractDailyResult(result_date, close_price)

        self.trade_count: int = 0
        self.turnover: float = 0
        self.commission: float = 0
        self.trading_pnl: float = 0
        self.holding_pnl: float = 0
        self.total_pnl: float = 0
        self.net_pnl: float = 0

    def add_trade(self, trade: TradeData) -> None:
        """Add trade information."""
        contract_result: ContractDailyResult = self.contract_results[trade.vt_symbol]
        contract_result.add_trade(trade)

    def calculate_pnl(
        self,
        pre_closes: dict[str, float],
        start_poses: dict[str, float],
        sizes: dict[str, float],
        long_rates: dict[str, float],
        short_rates: dict[str, float],
    ) -> None:
        """Calculate profit and loss."""
        self.pre_closes = pre_closes
        self.start_poses = start_poses

        for vt_symbol, contract_result in self.contract_results.items():
            contract_result.calculate_pnl(
                pre_closes.get(vt_symbol, 0),
                start_poses.get(vt_symbol, 0),
                sizes[vt_symbol],
                long_rates[vt_symbol],
                short_rates[vt_symbol],
            )

            self.trade_count += contract_result.trade_count
            self.turnover += contract_result.turnover
            self.commission += contract_result.commission
            self.trading_pnl += contract_result.trading_pnl
            self.holding_pnl += contract_result.holding_pnl
            self.total_pnl += contract_result.total_pnl
            self.net_pnl += contract_result.net_pnl

            self.end_poses[vt_symbol] = contract_result.end_pos

    def update_close_prices(self, close_prices: dict[str, float]) -> None:
        """Update daily close prices."""
        self.close_prices.update(close_prices)

        for vt_symbol, close_price in close_prices.items():
            contract_result: ContractDailyResult | None = self.contract_results.get(vt_symbol, None)
            if contract_result:
                contract_result.update_close_price(close_price)
            else:
                self.contract_results[vt_symbol] = ContractDailyResult(self.date, close_price)


def build_daily_result_dataframe(daily_results: dict[date, PortfolioDailyResult]) -> pl.DataFrame:
    """Convert portfolio daily results to the existing result DataFrame schema."""
    results: dict = defaultdict(list)

    for daily_result in daily_results.values():
        fields: list = [
            "date", "trade_count", "turnover",
            "commission", "trading_pnl",
            "holding_pnl", "total_pnl", "net_pnl",
        ]
        for key in fields:
            value = getattr(daily_result, key)
            results[key].append(value)

    return pl.DataFrame([
        pl.Series("date", results["date"], dtype=pl.Date),
        pl.Series("trade_count", results["trade_count"], dtype=pl.Int64),
        pl.Series("turnover", results["turnover"], dtype=pl.Float64),
        pl.Series("commission", results["commission"], dtype=pl.Float64),
        pl.Series("trading_pnl", results["trading_pnl"], dtype=pl.Float64),
        pl.Series("holding_pnl", results["holding_pnl"], dtype=pl.Float64),
        pl.Series("total_pnl", results["total_pnl"], dtype=pl.Float64),
        pl.Series("net_pnl", results["net_pnl"], dtype=pl.Float64),
    ])
