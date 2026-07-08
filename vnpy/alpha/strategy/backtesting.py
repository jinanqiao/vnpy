from collections import defaultdict
from datetime import date, datetime
from copy import copy

import polars as pl
from tqdm import tqdm

from vnpy.trader.constant import Direction, Offset, Interval, Status
from vnpy.trader.object import OrderData, TradeData, BarData
from vnpy.trader.utility import round_to, extract_vt_symbol

from ..logger import logger
from ..lab import AlphaLab
from .accounting import PortfolioAccount
from .matching import OrderMatcher
from .replay import BacktestReplayCoordinator
from .reporting import BacktestChartRenderer
from .settlement import ContractDailyResult, PortfolioDailyResult, build_daily_result_dataframe
from .statistics import BacktestStatisticsResult, calculate_backtest_statistics
from .template import AlphaStrategy


class BacktestingEngine:
    """Alpha strategy backtesting engine"""

    gateway_name: str = "BACKTESTING"

    def __init__(self, lab: AlphaLab) -> None:
        """Constructor"""
        self.lab: AlphaLab = lab

        self.vt_symbols: list[str] = []
        self.start: datetime
        self.end: datetime

        self.long_rates: dict[str, float] = {}
        self.short_rates: dict[str, float] = {}
        self.sizes: dict[str, float] = {}
        self.priceticks: dict[str, float] = {}

        self.capital: float = 0
        self.risk_free: float = 0
        self.annual_days: int = 0

        self.strategy_class: type[AlphaStrategy]
        self.strategy: AlphaStrategy
        self.bars: dict[str, BarData] = {}
        self.datetime: datetime | None = None

        self.interval: Interval
        self.history_data: dict[tuple, BarData] = {}
        self.dts: set[datetime] = set()

        self.limit_order_count: int = 0
        self.limit_orders: dict[str, OrderData] = {}
        self.active_limit_orders: dict[str, OrderData] = {}

        self.trade_count: int = 0
        self.trades: dict[str, TradeData] = {}

        self.logs: list[str] = []

        self.daily_results: dict[date, PortfolioDailyResult] = {}
        self.daily_df: pl.DataFrame

        self.pre_closes: defaultdict = defaultdict(float)

        self.cash: float = 0
        self.signal_df: pl.DataFrame
        self.portfolio_account: PortfolioAccount = PortfolioAccount()
        self.order_matcher: OrderMatcher = OrderMatcher()
        self.replay_coordinator: BacktestReplayCoordinator = BacktestReplayCoordinator()
        self.chart_renderer: BacktestChartRenderer = BacktestChartRenderer()

    def set_parameters(
        self,
        vt_symbols: list[str],
        interval: Interval,
        start: datetime,
        end: datetime,
        capital: int = 1_000_000,
        risk_free: float = 0,
        annual_days: int = 240
    ) -> None:
        """Set parameters"""
        self.vt_symbols = vt_symbols
        self.interval = interval

        self.start = start
        self.end = end
        self.capital = capital
        self.risk_free = risk_free
        self.annual_days = annual_days

        self.cash = capital

        contract_settings: dict = self.lab.load_contract_setttings()
        for vt_symbol in vt_symbols:
            setting: dict | None = contract_settings.get(vt_symbol, None)
            if not setting:
                logger.warning(f"找不到合约{vt_symbol}的交易配置，请检查！")
                continue

            self.long_rates[vt_symbol] = setting["long_rate"]
            self.short_rates[vt_symbol] = setting["short_rate"]
            self.sizes[vt_symbol] = setting["size"]
            self.priceticks[vt_symbol] = setting["pricetick"]

    def add_strategy(self, strategy_class: type, setting: dict, signal_df: pl.DataFrame) -> None:
        """Add strategy"""
        self.strategy_class = strategy_class
        self.strategy = strategy_class(
            self, strategy_class.__name__, copy(self.vt_symbols), setting
        )
        self.signal_df = signal_df

    def load_data(self) -> None:
        """Load historical data"""
        logger.info("开始加载历史数据")

        if not self.end:
            self.end = datetime.now()

        if self.start >= self.end:
            logger.info("起始日期必须小于结束日期")
            return

        # Clear previously loaded historical data
        self.history_data.clear()
        self.dts.clear()

        # Load historical data for each symbol
        empty_symbols: list[str] = []
        for vt_symbol in tqdm(self.vt_symbols, total=len(self.vt_symbols)):
            data: list[BarData] = self.lab.load_bar_data(
                vt_symbol,
                self.interval,
                self.start,
                self.end
            )

            for bar in data:
                self.dts.add(bar.datetime)
                self.history_data[(bar.datetime, vt_symbol)] = bar

            data_count = len(data)
            if not data_count:
                empty_symbols.append(vt_symbol)

        if empty_symbols:
            logger.info(f"部分合约历史数据为空：{empty_symbols}")

        logger.info("所有历史数据加载完成")

    def run_backtesting(self) -> None:
        """Start backtesting"""
        replay_coordinator: BacktestReplayCoordinator = getattr(
            self,
            "replay_coordinator",
            BacktestReplayCoordinator(),
        )
        replay_coordinator.run(self)

    def calculate_result(self) -> pl.DataFrame | None:
        """Calculate daily mark-to-market profit and loss"""
        logger.info("开始计算逐日盯市盈亏")

        if not self.trades:
            logger.info("成交记录为空，无法计算")
            return None

        for trade in self.trades.values():
            if not trade.datetime:
                continue

            d: date = trade.datetime.date()
            daily_result: PortfolioDailyResult = self.daily_results[d]
            daily_result.add_trade(trade)

        pre_closes: dict[str, float] = {}
        start_poses: dict[str, float] = {}

        for daily_result in self.daily_results.values():
            daily_result.calculate_pnl(
                pre_closes,
                start_poses,
                self.sizes,
                self.long_rates,
                self.short_rates
            )

            pre_closes = daily_result.close_prices
            start_poses = daily_result.end_poses

        if self.daily_results:
            self.daily_df = build_daily_result_dataframe(self.daily_results)

        logger.info("逐日盯市盈亏计算完成")
        return self.daily_df

    def calculate_statistics(self) -> dict:
        """Calculate strategy statistics"""
        logger.info("开始计算策略统计指标")

        result: BacktestStatisticsResult = calculate_backtest_statistics(
            self.daily_df,
            self.capital,
            self.risk_free,
            self.annual_days,
        )
        self.daily_df = result.daily_df
        statistics: dict = result.statistics

        if not result.positive_balance:
            logger.info("回测中出现爆仓（资金小于等于0），无法计算策略统计指标")

        # Output results
        logger.info("-" * 30)
        logger.info(f"首个交易日：  {statistics['start_date']}")
        logger.info(f"最后交易日：  {statistics['end_date']}")

        logger.info(f"总交易日：  {statistics['total_days']}")
        logger.info(f"盈利交易日：  {statistics['profit_days']}")
        logger.info(f"亏损交易日：  {statistics['loss_days']}")

        logger.info(f"起始资金：  {self.capital:,.2f}")
        logger.info(f"结束资金：  {statistics['end_balance']:,.2f}")

        logger.info(f"总收益率：  {statistics['total_return']:,.2f}%")
        logger.info(f"年化收益：  {statistics['annual_return']:,.2f}%")
        logger.info(f"最大回撤:   {statistics['max_drawdown']:,.2f}")
        logger.info(f"百分比最大回撤: {statistics['max_ddpercent']:,.2f}%")
        logger.info(f"最长回撤天数:   {statistics['max_drawdown_duration']}")

        logger.info(f"总盈亏：  {statistics['total_net_pnl']:,.2f}")
        logger.info(f"总手续费：  {statistics['total_commission']:,.2f}")
        logger.info(f"总成交金额：  {statistics['total_turnover']:,.2f}")
        logger.info(f"总成交笔数：  {statistics['total_trade_count']}")

        logger.info(f"日均盈亏：  {statistics['daily_net_pnl']:,.2f}")
        logger.info(f"日均手续费：  {statistics['daily_commission']:,.2f}")
        logger.info(f"日均成交金额：  {statistics['daily_turnover']:,.2f}")
        logger.info(f"日均成交笔数：  {statistics['daily_trade_count']}")

        logger.info(f"日均收益率：  {statistics['daily_return']:,.2f}%")
        logger.info(f"收益标准差：  {statistics['return_std']:,.2f}%")
        logger.info(f"Sharpe Ratio：  {statistics['sharpe_ratio']:,.2f}")
        logger.info(f"收益回撤比：  {statistics['return_drawdown_ratio']:,.2f}")

        logger.info("策略统计指标计算完成")
        return statistics

    def show_chart(self) -> None:
        """Display chart"""
        chart_renderer: BacktestChartRenderer = getattr(self, "chart_renderer", BacktestChartRenderer())
        chart_renderer.show_chart(self.daily_df)

    def show_performance(self, benchmark_symbol: str) -> None:
        """Display performance metrics"""
        # Load benchmark prices
        benchmark_bars: list[BarData] = self.lab.load_bar_data(benchmark_symbol, self.interval, self.start, self.end)

        benchmark_prices: list[float] = [bar.close_price for bar in benchmark_bars]
        chart_renderer: BacktestChartRenderer = getattr(self, "chart_renderer", BacktestChartRenderer())
        chart_renderer.show_performance(self.daily_df, benchmark_prices)

    def update_daily_close(self, bars: dict[str, BarData], dt: datetime) -> None:
        """Update daily closing price"""
        d: date = dt.date()

        close_prices: dict[str, float] = {}
        for bar in bars.values():
            if not bar.close_price:
                close_prices[bar.vt_symbol] = self.pre_closes[bar.vt_symbol]
            else:
                close_prices[bar.vt_symbol] = bar.close_price

        daily_result: PortfolioDailyResult | None = self.daily_results.get(d, None)

        if daily_result:
            daily_result.update_close_prices(close_prices)
        else:
            self.daily_results[d] = PortfolioDailyResult(d, close_prices)

    def new_bars(self, dt: datetime) -> None:
        """Push historical data"""
        self.datetime = dt

        bars: dict[str, BarData] = {}
        for vt_symbol in self.vt_symbols:
            last_bar = self.bars.get(vt_symbol, None)
            if last_bar:
                if last_bar.close_price:
                    self.pre_closes[vt_symbol] = last_bar.close_price

            bar: BarData | None = self.history_data.get((dt, vt_symbol), None)

            # Check if historical data for the specified time of the contract is obtained
            if bar:
                # Update K-line for order matching
                self.bars[vt_symbol] = bar
                # Cache K-line data for strategy.on_bars update
                bars[vt_symbol] = bar
            # If not available, but there is contract data cached in the self.bars dictionary, use previous data to fill
            elif vt_symbol in self.bars:
                old_bar: BarData = self.bars[vt_symbol]

                fill_bar: BarData = BarData(
                    symbol=old_bar.symbol,
                    exchange=old_bar.exchange,
                    datetime=dt,
                    open_price=old_bar.close_price,
                    high_price=old_bar.close_price,
                    low_price=old_bar.close_price,
                    close_price=old_bar.close_price,
                    gateway_name=old_bar.gateway_name
                )
                self.bars[vt_symbol] = fill_bar

        self.cross_order()
        self.strategy.on_bars(bars)

        self.update_daily_close(self.bars, dt)

    def cross_order(self) -> None:
        """Match limit orders"""
        order_matcher: OrderMatcher = getattr(self, "order_matcher", OrderMatcher())
        portfolio_account: PortfolioAccount = getattr(self, "portfolio_account", PortfolioAccount())

        for order in list(self.active_limit_orders.values()):
            bar: BarData = self.bars[order.vt_symbol]

            # Push order status update for unfilled orders
            if order.status == Status.SUBMITTING:
                order.status = Status.NOTTRADED
                self.strategy.update_order(order)

            # Calculate price limits
            pricetick: float = self.priceticks[order.vt_symbol]
            pre_close: float = self.pre_closes.get(order.vt_symbol, 0)
            match = order_matcher.match_limit_order(order, bar, pre_close, pricetick)
            if not match:
                continue

            # Push order status update for filled orders
            order.traded = order.volume
            order.status = Status.ALLTRADED
            self.strategy.update_order(order)

            if order.vt_orderid in self.active_limit_orders:
                self.active_limit_orders.pop(order.vt_orderid)

            # Generate trade information
            self.trade_count += 1

            trade: TradeData = order_matcher.create_trade(
                order,
                str(self.trade_count),
                match.price,
                self.datetime,
                self.gateway_name,
            )

            # Update available funds
            size: float = self.sizes[trade.vt_symbol]
            self.cash = portfolio_account.apply_trade(
                self.cash,
                trade,
                size,
                self.long_rates[trade.vt_symbol],
                self.short_rates[trade.vt_symbol],
            )

            # Push trade information
            self.strategy.update_trade(trade)
            self.trades[trade.vt_tradeid] = trade

    def get_signal(self) -> pl.DataFrame:
        """Get model prediction signal for current time"""
        if not self.datetime:
            self.write_log("尚未开始数据回放，无法加载模型预测值")
            return pl.DataFrame()

        dt: datetime = self.datetime.replace(tzinfo=None)
        signal: pl.DataFrame = self.signal_df.filter(pl.col("datetime") == dt)

        if signal.is_empty():
            self.write_log(f"找不到{dt}对应的信号模型预测值")

        return signal

    def send_order(
        self,
        strategy: AlphaStrategy,
        vt_symbol: str,
        direction: Direction,
        offset: Offset,
        price: float,
        volume: float,
    ) -> list[str]:
        """Send order"""
        price = round_to(price, self.priceticks[vt_symbol])
        symbol, exchange = extract_vt_symbol(vt_symbol)

        self.limit_order_count += 1

        order: OrderData = OrderData(
            symbol=symbol,
            exchange=exchange,
            orderid=str(self.limit_order_count),
            direction=direction,
            offset=offset,
            price=price,
            volume=volume,
            status=Status.SUBMITTING,
            datetime=self.datetime,
            gateway_name=self.gateway_name,
        )

        self.active_limit_orders[order.vt_orderid] = order
        self.limit_orders[order.vt_orderid] = order

        return [order.vt_orderid]

    def cancel_order(self, strategy: AlphaStrategy, vt_orderid: str) -> None:
        """Cancel order"""
        if vt_orderid not in self.active_limit_orders:
            return
        order: OrderData = self.active_limit_orders.pop(vt_orderid)

        order.status = Status.CANCELLED
        self.strategy.update_order(order)

    def write_log(self, msg: str, strategy: AlphaStrategy | None = None) -> None:
        """Output log message"""
        msg = f"{self.datetime}  {msg}"
        self.logs.append(msg)

    def get_all_trades(self) -> list[TradeData]:
        """Get all trade information"""
        return list(self.trades.values())

    def get_all_orders(self) -> list[OrderData]:
        """Get all order information"""
        return list(self.limit_orders.values())

    def get_all_daily_results(self) -> list["PortfolioDailyResult"]:
        """Get all daily profit and loss information"""
        return list(self.daily_results.values())

    def get_cash_available(self) -> float:
        """Get current available cash"""
        return self.cash

    def get_holding_value(self) -> float:
        """Get current holding market value"""
        holding_value: float = 0

        for vt_symbol, pos in self.strategy.pos_data.items():
            bar: BarData = self.bars[vt_symbol]
            size: float = self.sizes[vt_symbol]

            holding_value += bar.close_price * pos * size

        return holding_value
