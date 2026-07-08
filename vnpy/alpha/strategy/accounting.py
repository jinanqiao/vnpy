from dataclasses import dataclass

from vnpy.trader.constant import Direction
from vnpy.trader.object import TradeData


@dataclass(frozen=True)
class TradeCashFlow:
    """Cash-flow details for a filled trade."""

    turnover: float
    commission: float
    cash_delta: float


class PortfolioAccount:
    """Portfolio cash accounting helpers for alpha backtests."""

    def calculate_trade_cash_flow(
        self,
        trade: TradeData,
        size: float,
        long_rate: float,
        short_rate: float,
    ) -> TradeCashFlow:
        """Calculate turnover, commission, and cash delta for one trade."""
        turnover: float = trade.price * trade.volume * size

        if trade.direction == Direction.LONG:
            commission: float = turnover * long_rate
            cash_delta: float = -turnover - commission
        else:
            commission = turnover * short_rate
            cash_delta = turnover - commission

        return TradeCashFlow(
            turnover=turnover,
            commission=commission,
            cash_delta=cash_delta,
        )

    def apply_trade(
        self,
        cash: float,
        trade: TradeData,
        size: float,
        long_rate: float,
        short_rate: float,
    ) -> float:
        """Apply a filled trade to cash and return the updated value."""
        cash_flow: TradeCashFlow = self.calculate_trade_cash_flow(
            trade,
            size,
            long_rate,
            short_rate,
        )
        return cash + cash_flow.cash_delta
