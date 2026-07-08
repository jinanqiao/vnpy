from __future__ import annotations

import traceback
from typing import Protocol

from ..logger import logger


class BacktestReplayEngine(Protocol):
    strategy: object
    dts: set

    def new_bars(self, dt) -> None:
        ...


class BacktestReplayCoordinator:
    """Coordinate historical replay for a backtesting engine."""

    def run(self, engine: BacktestReplayEngine) -> None:
        engine.strategy.on_init()
        logger.info("策略初始化完成")

        dts: list = list(engine.dts)
        dts.sort()

        logger.info("开始回放历史数据")
        for dt in dts:
            try:
                engine.new_bars(dt)
            except Exception:
                logger.info("触发异常，回测终止")
                logger.info(traceback.format_exc())
                return

        logger.info("历史数据回放结束")
