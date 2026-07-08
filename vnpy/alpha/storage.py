import json
import pickle
import shelve
from collections import defaultdict
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import polars as pl

from vnpy.trader.constant import Interval
from vnpy.trader.object import BarData
from vnpy.trader.utility import extract_vt_symbol

from .dataset import to_datetime
from .logger import logger


class BarRepository:
    """Bar data repository preserving the existing AlphaLab parquet layout."""

    def __init__(self, daily_path: Path, minute_path: Path) -> None:
        self.daily_path = daily_path
        self.minute_path = minute_path

    def save(self, bars: list[BarData]) -> None:
        """Save bar data."""
        if not bars:
            return

        bar: BarData = bars[0]
        file_path = self._file_path(bar.vt_symbol, bar.interval)
        if file_path is None:
            return

        data: list = []
        for bar in bars:
            data.append({
                "datetime": bar.datetime.replace(tzinfo=None),
                "open": bar.open_price,
                "high": bar.high_price,
                "low": bar.low_price,
                "close": bar.close_price,
                "volume": bar.volume,
                "turnover": bar.turnover,
                "open_interest": bar.open_interest,
            })

        new_df: pl.DataFrame = pl.DataFrame(data)

        if file_path.exists():
            old_df: pl.DataFrame = pl.read_parquet(file_path)
            new_df = pl.concat([old_df, new_df])
            new_df = new_df.unique(subset=["datetime"])
            new_df = new_df.sort("datetime")

        new_df.write_parquet(file_path)

    def load(
        self,
        vt_symbol: str,
        interval: Interval | str,
        start: datetime | str,
        end: datetime | str,
    ) -> list[BarData]:
        """Load bar data."""
        if isinstance(interval, str):
            interval = Interval(interval)

        start = to_datetime(start)
        end = to_datetime(end)

        file_path = self._file_path(vt_symbol, interval)
        if file_path is None:
            return []

        if not file_path.exists():
            logger.error(f"File {file_path} does not exist")
            return []

        df: pl.DataFrame = pl.read_parquet(file_path)
        df = df.filter((pl.col("datetime") >= start) & (pl.col("datetime") <= end))

        bars: list[BarData] = []
        symbol, exchange = extract_vt_symbol(vt_symbol)

        for row in df.iter_rows(named=True):
            bars.append(BarData(
                symbol=symbol,
                exchange=exchange,
                datetime=row["datetime"],
                interval=interval,
                open_price=row["open"],
                high_price=row["high"],
                low_price=row["low"],
                close_price=row["close"],
                volume=row["volume"],
                turnover=row["turnover"],
                open_interest=row["open_interest"],
                gateway_name="DB",
            ))

        return bars

    def load_df(
        self,
        vt_symbols: list[str],
        interval: Interval | str,
        start: datetime | str,
        end: datetime | str,
        extended_days: int,
    ) -> pl.DataFrame | None:
        """Load bar data as a normalized DataFrame."""
        if not vt_symbols:
            return None

        if isinstance(interval, str):
            interval = Interval(interval)

        start = to_datetime(start) - timedelta(days=extended_days)
        end = to_datetime(end) + timedelta(days=extended_days // 10)

        dfs: list = []

        for vt_symbol in vt_symbols:
            file_path = self._file_path(vt_symbol, interval)
            if file_path is None:
                return None

            if not file_path.exists():
                logger.error(f"File {file_path} does not exist")
                continue

            df: pl.DataFrame = pl.read_parquet(file_path)
            df = df.filter((pl.col("datetime") >= start) & (pl.col("datetime") <= end))
            df = df.with_columns(
                pl.col("open"),
                pl.col("high"),
                pl.col("low"),
                pl.col("close"),
                pl.col("volume"),
                pl.col("turnover"),
                pl.col("open_interest"),
                (pl.col("turnover") / pl.col("volume")).alias("vwap"),
            )

            if df.is_empty():
                continue

            close_0: float = df.select(pl.col("close")).item(0, 0)
            df = df.with_columns(
                (pl.col("open") / close_0).alias("open"),
                (pl.col("high") / close_0).alias("high"),
                (pl.col("low") / close_0).alias("low"),
                (pl.col("close") / close_0).alias("close"),
            )

            numeric_columns: list = df.columns[1:]
            mask: pl.Series = df[numeric_columns].sum_horizontal() == 0
            df = df.with_columns(
                [pl.when(mask).then(float("nan")).otherwise(pl.col(col)).alias(col) for col in numeric_columns]
            )
            df = df.with_columns(pl.lit(vt_symbol).alias("vt_symbol"))
            dfs.append(df)

        if not dfs:
            return None
        return pl.concat(dfs)

    def _file_path(self, vt_symbol: str, interval: Interval | None) -> Path | None:
        if interval == Interval.DAILY:
            return self.daily_path.joinpath(f"{vt_symbol}.parquet")
        if interval == Interval.MINUTE:
            return self.minute_path.joinpath(f"{vt_symbol}.parquet")
        if interval:
            logger.error(f"Unsupported interval {interval.value}")
        return None


class ComponentRepository:
    """Index component repository preserving the existing shelve layout."""

    def __init__(self, component_path: Path) -> None:
        self.component_path = component_path

    def save(self, index_symbol: str, index_components: dict[str, list[str]]) -> None:
        file_path: Path = self.component_path.joinpath(f"{index_symbol}")
        with shelve.open(str(file_path)) as db:
            db.update(index_components)

    @lru_cache
    def load(self, index_symbol: str, start: datetime | str, end: datetime | str) -> dict[datetime, list[str]]:
        file_path: Path = self.component_path.joinpath(f"{index_symbol}")

        start = to_datetime(start)
        end = to_datetime(end)

        with shelve.open(str(file_path)) as db:
            keys: list[str] = list(db.keys())
            keys.sort()

            index_components: dict[datetime, list[str]] = {}
            for key in keys:
                dt: datetime = datetime.strptime(key, "%Y-%m-%d")
                if start <= dt <= end:
                    index_components[dt] = db[key]

            return index_components

    def symbols(self, index_symbol: str, start: datetime | str, end: datetime | str) -> list[str]:
        index_components = self.load(index_symbol, start, end)
        component_symbols: set[str] = set()

        for vt_symbols in index_components.values():
            component_symbols.update(vt_symbols)

        return list(component_symbols)

    def filters(self, index_symbol: str, start: datetime | str, end: datetime | str) -> dict[str, list[tuple[datetime, datetime]]]:
        index_components = self.load(index_symbol, start, end)
        trading_dates: list[datetime] = sorted(index_components.keys())
        component_filters: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)

        all_symbols: set[str] = set()
        for vt_symbols in index_components.values():
            all_symbols.update(vt_symbols)

        for vt_symbol in all_symbols:
            period_start: datetime | None = None
            period_end: datetime | None = None

            for trading_date in trading_dates:
                if vt_symbol in index_components[trading_date]:
                    if period_start is None:
                        period_start = trading_date
                    period_end = trading_date
                else:
                    if period_start and period_end:
                        component_filters[vt_symbol].append((period_start, period_end))
                        period_start = None
                        period_end = None

            if period_start and period_end:
                component_filters[vt_symbol].append((period_start, period_end))

        return component_filters


class ContractRepository:
    """Contract setting repository preserving the existing JSON layout."""

    def __init__(self, contract_path: Path) -> None:
        self.contract_path = contract_path

    def add(self, vt_symbol: str, long_rate: float, short_rate: float, size: float, pricetick: float) -> None:
        contracts: dict = self.load()
        contracts[vt_symbol] = {
            "long_rate": long_rate,
            "short_rate": short_rate,
            "size": size,
            "pricetick": pricetick,
        }

        with open(self.contract_path, mode="w+", encoding="UTF-8") as f:
            json.dump(contracts, f, indent=4, ensure_ascii=False)

    def load(self) -> dict:
        contracts: dict = {}

        if self.contract_path.exists():
            with open(self.contract_path, encoding="UTF-8") as f:
                contracts = json.load(f)

        return contracts


class PickleArtifactStore:
    """Pickle-backed artifact store preserving existing dataset/model layout."""

    def __init__(self, folder_path: Path, label: str) -> None:
        self.folder_path = folder_path
        self.label = label

    def save(self, name: str, artifact: Any) -> None:
        file_path: Path = self.folder_path.joinpath(f"{name}.pkl")
        with open(file_path, mode="wb") as f:
            pickle.dump(artifact, f)

    def load(self, name: str) -> Any | None:
        file_path: Path = self.folder_path.joinpath(f"{name}.pkl")
        if not file_path.exists():
            logger.error(f"{self.label} file {name} does not exist")
            return None

        with open(file_path, mode="rb") as f:
            return pickle.load(f)

    def remove(self, name: str) -> bool:
        file_path: Path = self.folder_path.joinpath(f"{name}.pkl")
        if not file_path.exists():
            logger.error(f"{self.label} file {name} does not exist")
            return False

        file_path.unlink()
        return True

    def list_all(self) -> list[str]:
        return [file.stem for file in self.folder_path.glob("*.pkl")]


class SignalStore:
    """Parquet-backed signal store preserving existing signal layout."""

    def __init__(self, signal_path: Path) -> None:
        self.signal_path = signal_path

    def save(self, name: str, signal: pl.DataFrame) -> None:
        file_path: Path = self.signal_path.joinpath(f"{name}.parquet")
        signal.write_parquet(file_path)

    def load(self, name: str) -> pl.DataFrame | None:
        file_path: Path = self.signal_path.joinpath(f"{name}.parquet")
        if not file_path.exists():
            logger.error(f"Signal file {name} does not exist")
            return None

        return pl.read_parquet(file_path)

    def remove(self, name: str) -> bool:
        file_path: Path = self.signal_path.joinpath(f"{name}.parquet")
        if not file_path.exists():
            logger.error(f"Signal file {name} does not exist")
            return False

        file_path.unlink()
        return True

    def list_all(self) -> list[str]:
        return [file.stem for file in self.signal_path.glob("*.parquet")]
