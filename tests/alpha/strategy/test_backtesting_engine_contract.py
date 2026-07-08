from datetime import date, datetime
from types import SimpleNamespace

import polars as pl
import pytest

from vnpy.alpha.strategy.backtesting import BacktestingEngine, ContractDailyResult, PortfolioDailyResult
from vnpy.trader.constant import Direction, Exchange, Offset, Status
from vnpy.trader.object import BarData, OrderData, TradeData


VT_SYMBOL = "000001.SSE"


class RecordingStrategy:
    def __init__(self) -> None:
        self.orders: list[OrderData] = []
        self.trades: list[TradeData] = []
        self.pos_data: dict[str, float] = {}

    def update_order(self, order: OrderData) -> None:
        self.orders.append(order)

    def update_trade(self, trade: TradeData) -> None:
        self.trades.append(trade)


class ReplayStrategy:
    def __init__(self, events: list) -> None:
        self.events = events

    def on_init(self) -> None:
        self.events.append("init")


class RecordingChartRenderer:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def show_chart(self, daily_df: pl.DataFrame) -> None:
        self.calls.append(("chart", daily_df))

    def show_performance(self, daily_df: pl.DataFrame, benchmark_prices: list[float]) -> None:
        self.calls.append(("performance", daily_df, benchmark_prices))


def make_bar(
    open_price: float = 10.0,
    high_price: float = 11.0,
    low_price: float = 9.0,
    close_price: float = 10.5,
) -> BarData:
    return BarData(
        symbol="000001",
        exchange=Exchange.SSE,
        datetime=datetime(2024, 1, 2),
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        gateway_name="TEST",
    )


def make_order(direction: Direction, price: float, volume: float = 2) -> OrderData:
    return OrderData(
        symbol="000001",
        exchange=Exchange.SSE,
        orderid="1",
        direction=direction,
        offset=Offset.OPEN,
        price=price,
        volume=volume,
        status=Status.SUBMITTING,
        datetime=datetime(2024, 1, 2),
        gateway_name="BACKTESTING",
    )


def make_trade(direction: Direction, price: float, volume: float = 2) -> TradeData:
    return TradeData(
        symbol="000001",
        exchange=Exchange.SSE,
        orderid="1",
        tradeid="1",
        direction=direction,
        offset=Offset.OPEN,
        price=price,
        volume=volume,
        datetime=datetime(2024, 1, 2),
        gateway_name="BACKTESTING",
    )


def make_engine(order: OrderData, bar: BarData | None = None, cash: float = 10_000) -> BacktestingEngine:
    engine = BacktestingEngine.__new__(BacktestingEngine)
    strategy = RecordingStrategy()
    engine.strategy = strategy
    engine.bars = {VT_SYMBOL: bar or make_bar()}
    engine.datetime = datetime(2024, 1, 2)
    engine.priceticks = {VT_SYMBOL: 0.01}
    engine.pre_closes = {VT_SYMBOL: 10.0}
    engine.sizes = {VT_SYMBOL: 100}
    engine.long_rates = {VT_SYMBOL: 0.001}
    engine.short_rates = {VT_SYMBOL: 0.002}
    engine.cash = cash
    engine.trade_count = 0
    engine.trades = {}
    engine.limit_orders = {order.vt_orderid: order}
    engine.active_limit_orders = {order.vt_orderid: order}
    engine.logs = []
    return engine


def test_long_limit_order_crosses_at_lesser_of_order_and_open_price() -> None:
    order = make_order(Direction.LONG, price=10.5)
    engine = make_engine(order, make_bar(open_price=10.0, low_price=9.5))

    engine.cross_order()

    trade = list(engine.trades.values())[0]
    assert order.status == Status.ALLTRADED
    assert trade.direction == Direction.LONG
    assert trade.price == 10.0
    assert trade.volume == 2


def test_short_limit_order_crosses_at_greater_of_order_and_open_price() -> None:
    order = make_order(Direction.SHORT, price=9.5)
    engine = make_engine(order, make_bar(open_price=10.0, high_price=10.5))

    engine.cross_order()

    trade = list(engine.trades.values())[0]
    assert order.status == Status.ALLTRADED
    assert trade.direction == Direction.SHORT
    assert trade.price == 10.0
    assert trade.volume == 2


def test_limit_up_bar_blocks_long_fill() -> None:
    order = make_order(Direction.LONG, price=11.0)
    engine = make_engine(order, make_bar(open_price=11.0, low_price=11.0, high_price=11.0))

    engine.cross_order()

    assert engine.trades == {}
    assert order.status == Status.NOTTRADED
    assert order.vt_orderid in engine.active_limit_orders


def test_limit_down_bar_blocks_short_fill() -> None:
    order = make_order(Direction.SHORT, price=9.0)
    engine = make_engine(order, make_bar(open_price=9.0, low_price=9.0, high_price=9.0))

    engine.cross_order()

    assert engine.trades == {}
    assert order.status == Status.NOTTRADED
    assert order.vt_orderid in engine.active_limit_orders


def test_long_trade_reduces_cash_by_turnover_and_commission() -> None:
    order = make_order(Direction.LONG, price=10.0)
    engine = make_engine(order, make_bar(open_price=10.0, low_price=9.5), cash=10_000)

    engine.cross_order()

    assert engine.cash == pytest.approx(10_000 - 10.0 * 2 * 100 - 10.0 * 2 * 100 * 0.001)


def test_short_trade_increases_cash_net_of_commission() -> None:
    order = make_order(Direction.SHORT, price=10.0)
    engine = make_engine(order, make_bar(open_price=10.0, high_price=10.5), cash=10_000)

    engine.cross_order()

    assert engine.cash == pytest.approx(10_000 + 10.0 * 2 * 100 - 10.0 * 2 * 100 * 0.002)


def test_contract_daily_result_calculates_holding_and_trading_pnl() -> None:
    result = ContractDailyResult(date(2024, 1, 2), close_price=12.0)
    result.add_trade(make_trade(Direction.LONG, price=11.0, volume=2))

    result.calculate_pnl(pre_close=10.0, start_pos=3, size=100, long_rate=0.001, short_rate=0.002)

    assert result.holding_pnl == pytest.approx(3 * (12.0 - 10.0) * 100)
    assert result.trading_pnl == pytest.approx(2 * (12.0 - 11.0) * 100)
    assert result.turnover == pytest.approx(2 * 100 * 11.0)
    assert result.commission == pytest.approx(2 * 100 * 11.0 * 0.001)
    assert result.end_pos == 5
    assert result.net_pnl == pytest.approx(result.holding_pnl + result.trading_pnl - result.commission)


def test_portfolio_daily_result_aggregates_contract_results() -> None:
    result = PortfolioDailyResult(date(2024, 1, 2), {VT_SYMBOL: 12.0, "000002.SSE": 20.0})
    result.add_trade(make_trade(Direction.LONG, price=11.0, volume=2))
    result.add_trade(
        TradeData(
            symbol="000002",
            exchange=Exchange.SSE,
            orderid="2",
            tradeid="2",
            direction=Direction.SHORT,
            offset=Offset.OPEN,
            price=21.0,
            volume=1,
            datetime=datetime(2024, 1, 2),
            gateway_name="BACKTESTING",
        )
    )

    result.calculate_pnl(
        pre_closes={VT_SYMBOL: 10.0, "000002.SSE": 22.0},
        start_poses={VT_SYMBOL: 3, "000002.SSE": 4},
        sizes={VT_SYMBOL: 100, "000002.SSE": 10},
        long_rates={VT_SYMBOL: 0.001, "000002.SSE": 0.001},
        short_rates={VT_SYMBOL: 0.002, "000002.SSE": 0.002},
    )

    contract_totals = list(result.contract_results.values())
    assert result.trade_count == 2
    assert result.turnover == pytest.approx(sum(item.turnover for item in contract_totals))
    assert result.commission == pytest.approx(sum(item.commission for item in contract_totals))
    assert result.trading_pnl == pytest.approx(sum(item.trading_pnl for item in contract_totals))
    assert result.holding_pnl == pytest.approx(sum(item.holding_pnl for item in contract_totals))
    assert result.total_pnl == pytest.approx(sum(item.total_pnl for item in contract_totals))
    assert result.net_pnl == pytest.approx(sum(item.net_pnl for item in contract_totals))


def test_calculate_statistics_preserves_keys_and_daily_columns() -> None:
    engine = BacktestingEngine.__new__(BacktestingEngine)
    engine.capital = 100_000
    engine.risk_free = 0
    engine.annual_days = 240
    engine.daily_df = pl.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
            "trade_count": [1, 2, 0],
            "turnover": [1_000.0, 2_000.0, 0.0],
            "commission": [1.0, 2.0, 0.0],
            "trading_pnl": [100.0, -50.0, 0.0],
            "holding_pnl": [0.0, 10.0, 20.0],
            "total_pnl": [100.0, -40.0, 20.0],
            "net_pnl": [99.0, -42.0, 20.0],
        }
    )

    statistics = engine.calculate_statistics()

    assert set(statistics) == {
        "start_date",
        "end_date",
        "total_days",
        "profit_days",
        "loss_days",
        "capital",
        "end_balance",
        "max_drawdown",
        "max_ddpercent",
        "max_drawdown_duration",
        "total_net_pnl",
        "daily_net_pnl",
        "total_commission",
        "daily_commission",
        "total_turnover",
        "daily_turnover",
        "total_trade_count",
        "daily_trade_count",
        "total_return",
        "annual_return",
        "daily_return",
        "return_std",
        "sharpe_ratio",
        "return_drawdown_ratio",
    }
    assert {"balance", "return", "highlevel", "drawdown", "ddpercent"}.issubset(engine.daily_df.columns)
    assert statistics["total_net_pnl"] == pytest.approx(77.0)
    assert statistics["end_balance"] == pytest.approx(100_077.0)


def test_run_backtesting_initializes_and_replays_in_datetime_order() -> None:
    engine = BacktestingEngine.__new__(BacktestingEngine)
    events: list = []
    engine.strategy = ReplayStrategy(events)
    engine.dts = {
        datetime(2024, 1, 3),
        datetime(2024, 1, 2),
    }

    def new_bars(dt: datetime) -> None:
        events.append(dt)

    engine.new_bars = new_bars

    engine.run_backtesting()

    assert events == ["init", datetime(2024, 1, 2), datetime(2024, 1, 3)]


def test_run_backtesting_stops_replay_on_error() -> None:
    engine = BacktestingEngine.__new__(BacktestingEngine)
    events: list = []
    engine.strategy = ReplayStrategy(events)
    engine.dts = {
        datetime(2024, 1, 2),
        datetime(2024, 1, 3),
    }

    def new_bars(dt: datetime) -> None:
        events.append(dt)
        if dt == datetime(2024, 1, 2):
            raise RuntimeError("stop")

    engine.new_bars = new_bars

    engine.run_backtesting()

    assert events == ["init", datetime(2024, 1, 2)]


def test_chart_and_performance_methods_delegate_to_renderer() -> None:
    engine = BacktestingEngine.__new__(BacktestingEngine)
    renderer = RecordingChartRenderer()
    engine.chart_renderer = renderer
    engine.daily_df = pl.DataFrame(
        {
            "date": [date(2024, 1, 2), date(2024, 1, 3)],
            "balance": [100_000.0, 101_000.0],
            "drawdown": [0.0, 0.0],
            "net_pnl": [0.0, 1_000.0],
            "commission": [0.0, 1.0],
            "turnover": [0.0, 10_000.0],
        }
    )
    benchmark_bars = [
        SimpleNamespace(close_price=10.0),
        SimpleNamespace(close_price=10.5),
    ]
    engine.lab = SimpleNamespace(load_bar_data=lambda symbol, interval, start, end: benchmark_bars)
    engine.interval = SimpleNamespace()
    engine.start = datetime(2024, 1, 2)
    engine.end = datetime(2024, 1, 3)

    engine.show_chart()
    engine.show_performance("000300.SSE")

    assert renderer.calls[0] == ("chart", engine.daily_df)
    assert renderer.calls[1] == ("performance", engine.daily_df, [10.0, 10.5])
