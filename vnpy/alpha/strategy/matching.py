from dataclasses import dataclass
from datetime import datetime

from vnpy.trader.constant import Direction
from vnpy.trader.object import BarData, OrderData, TradeData
from vnpy.trader.utility import round_to


@dataclass(frozen=True)
class LimitOrderMatch:
    """Result of a filled limit order in alpha backtesting."""

    price: float


class OrderMatcher:
    """Deterministic limit-order matcher for alpha backtests."""

    def match_limit_order(
        self,
        order: OrderData,
        bar: BarData,
        pre_close: float,
        pricetick: float,
    ) -> LimitOrderMatch | None:
        """Return fill details if the order crosses the bar."""
        long_cross_price: float = bar.low_price
        short_cross_price: float = bar.high_price

        limit_up: float = round_to(pre_close * 1.1, pricetick)
        limit_down: float = round_to(pre_close * 0.9, pricetick)

        long_cross: bool = (
            order.direction == Direction.LONG
            and order.price >= long_cross_price
            and long_cross_price > 0
            and bar.low_price < limit_up
        )

        short_cross: bool = (
            order.direction == Direction.SHORT
            and order.price <= short_cross_price
            and short_cross_price > 0
            and bar.high_price > limit_down
        )

        if long_cross:
            return LimitOrderMatch(price=min(order.price, bar.open_price))
        if short_cross:
            return LimitOrderMatch(price=max(order.price, bar.open_price))
        return None

    def create_trade(
        self,
        order: OrderData,
        tradeid: str,
        price: float,
        dt: datetime | None,
        gateway_name: str,
    ) -> TradeData:
        """Create a trade object for a matched order."""
        return TradeData(
            symbol=order.symbol,
            exchange=order.exchange,
            orderid=order.orderid,
            tradeid=tradeid,
            direction=order.direction,
            offset=order.offset,
            price=price,
            volume=order.volume,
            datetime=dt,
            gateway_name=gateway_name,
        )
