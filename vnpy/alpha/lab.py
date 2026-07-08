from pathlib import Path
from datetime import datetime

import polars as pl

from vnpy.trader.object import BarData
from vnpy.trader.constant import Interval

from .dataset import AlphaDataset
from .model import AlphaModel
from .storage import (
    BarRepository,
    ComponentRepository,
    ContractRepository,
    PickleArtifactStore,
    SignalStore,
)


class AlphaLab:
    """Alpha Research Laboratory"""

    def __init__(self, lab_path: str) -> None:
        """Constructor"""
        # Set data paths
        self.lab_path: Path = Path(lab_path)

        self.daily_path: Path = self.lab_path.joinpath("daily")
        self.minute_path: Path = self.lab_path.joinpath("minute")
        self.component_path: Path = self.lab_path.joinpath("component")

        self.dataset_path: Path = self.lab_path.joinpath("dataset")
        self.model_path: Path = self.lab_path.joinpath("model")
        self.signal_path: Path = self.lab_path.joinpath("signal")

        self.contract_path: Path = self.lab_path.joinpath("contract.json")

        # Create folders
        for path in [
            self.lab_path,
            self.daily_path,
            self.minute_path,
            self.component_path,
            self.dataset_path,
            self.model_path,
            self.signal_path
        ]:
            if not path.exists():
                path.mkdir(parents=True)

        self.bar_repository: BarRepository = BarRepository(self.daily_path, self.minute_path)
        self.component_repository: ComponentRepository = ComponentRepository(self.component_path)
        self.contract_repository: ContractRepository = ContractRepository(self.contract_path)
        self.dataset_store: PickleArtifactStore = PickleArtifactStore(self.dataset_path, "Dataset")
        self.model_store: PickleArtifactStore = PickleArtifactStore(self.model_path, "Model")
        self.signal_store: SignalStore = SignalStore(self.signal_path)

    def save_bar_data(self, bars: list[BarData]) -> None:
        """Save bar data"""
        self.bar_repository.save(bars)

    def load_bar_data(
        self,
        vt_symbol: str,
        interval: Interval | str,
        start: datetime | str,
        end: datetime | str
    ) -> list[BarData]:
        """Load bar data"""
        return self.bar_repository.load(vt_symbol, interval, start, end)

    def load_bar_df(
        self,
        vt_symbols: list[str],
        interval: Interval | str,
        start: datetime | str,
        end: datetime | str,
        extended_days: int
    ) -> pl.DataFrame | None:
        """Load bar data as DataFrame"""
        return self.bar_repository.load_df(vt_symbols, interval, start, end, extended_days)

    def save_component_data(
        self,
        index_symbol: str,
        index_components: dict[str, list[str]]
    ) -> None:
        """Save index component data"""
        self.component_repository.save(index_symbol, index_components)

    def load_component_data(
        self,
        index_symbol: str,
        start: datetime | str,
        end: datetime | str
    ) -> dict[datetime, list[str]]:
        """Load index component data as DataFrame"""
        return self.component_repository.load(index_symbol, start, end)

    def load_component_symbols(
        self,
        index_symbol: str,
        start: datetime | str,
        end: datetime | str
    ) -> list[str]:
        """Collect index component symbols"""
        return self.component_repository.symbols(index_symbol, start, end)

    def load_component_filters(
        self,
        index_symbol: str,
        start: datetime | str,
        end: datetime | str
    ) -> dict[str, list[tuple[datetime, datetime]]]:
        """Collect index component duration filters"""
        return self.component_repository.filters(index_symbol, start, end)

    def add_contract_setting(
        self,
        vt_symbol: str,
        long_rate: float,
        short_rate: float,
        size: float,
        pricetick: float
    ) -> None:
        """Add contract information"""
        self.contract_repository.add(vt_symbol, long_rate, short_rate, size, pricetick)

    def load_contract_setttings(self) -> dict:
        """Load contract settings"""
        return self.contract_repository.load()

    def save_dataset(self, name: str, dataset: AlphaDataset) -> None:
        """Save dataset"""
        self.dataset_store.save(name, dataset)

    def load_dataset(self, name: str) -> AlphaDataset | None:
        """Load dataset"""
        return self.dataset_store.load(name)

    def remove_dataset(self, name: str) -> bool:
        """Remove dataset"""
        return self.dataset_store.remove(name)

    def list_all_datasets(self) -> list[str]:
        """List all datasets"""
        return self.dataset_store.list_all()

    def save_model(self, name: str, model: AlphaModel) -> None:
        """Save model"""
        self.model_store.save(name, model)

    def load_model(self, name: str) -> AlphaModel | None:
        """Load model"""
        return self.model_store.load(name)

    def remove_model(self, name: str) -> bool:
        """Remove model"""
        return self.model_store.remove(name)

    def list_all_models(self) -> list[str]:
        """List all models"""
        return self.model_store.list_all()

    def save_signal(self, name: str, signal: pl.DataFrame) -> None:
        """Save signal"""
        self.signal_store.save(name, signal)

    def load_signal(self, name: str) -> pl.DataFrame | None:
        """Load signal"""
        return self.signal_store.load(name)

    def remove_signal(self, name: str) -> bool:
        """Remove signal"""
        return self.signal_store.remove(name)

    def list_all_signals(self) -> list[str]:
        """List all signals"""
        return self.signal_store.list_all()
