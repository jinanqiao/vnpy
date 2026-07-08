from datetime import datetime
from types import SimpleNamespace

import polars as pl

from vnpy.alpha.lab import AlphaLab
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData


def make_bar(dt: datetime, close_price: float) -> BarData:
    return BarData(
        symbol="000001",
        exchange=Exchange.SSE,
        datetime=dt,
        interval=Interval.DAILY,
        open_price=close_price - 1,
        high_price=close_price + 1,
        low_price=close_price - 2,
        close_price=close_price,
        volume=100,
        turnover=close_price * 100,
        open_interest=0,
        gateway_name="TEST",
    )


def test_alpha_lab_persists_bar_data_with_existing_layout(tmp_path) -> None:
    lab = AlphaLab(str(tmp_path))
    bars = [
        make_bar(datetime(2024, 1, 1), 10),
        make_bar(datetime(2024, 1, 2), 11),
    ]

    lab.save_bar_data(bars)
    loaded = lab.load_bar_data("000001.SSE", Interval.DAILY, "2024-01-01", "2024-01-02")

    assert (tmp_path / "daily" / "000001.SSE.parquet").exists()
    assert [bar.close_price for bar in loaded] == [10, 11]
    assert all(bar.gateway_name == "DB" for bar in loaded)


def test_alpha_lab_load_bar_df_keeps_normalized_dataframe_contract(tmp_path) -> None:
    lab = AlphaLab(str(tmp_path))
    lab.save_bar_data([
        make_bar(datetime(2024, 1, 1), 10),
        make_bar(datetime(2024, 1, 2), 20),
    ])

    df = lab.load_bar_df(["000001.SSE"], Interval.DAILY, "2024-01-01", "2024-01-02", extended_days=0)

    assert df is not None
    assert df["vt_symbol"].to_list() == ["000001.SSE", "000001.SSE"]
    assert df["close"].to_list() == [1.0, 2.0]
    assert "vwap" in df.columns


def test_alpha_lab_persists_components_and_contract_settings(tmp_path) -> None:
    lab = AlphaLab(str(tmp_path))
    components = {
        "2024-01-01": ["000001.SSE"],
        "2024-01-02": ["000001.SSE", "000002.SSE"],
    }

    lab.save_component_data("000300.SSE", components)
    loaded = lab.load_component_data("000300.SSE", "2024-01-01", "2024-01-02")
    symbols = lab.load_component_symbols("000300.SSE", "2024-01-01", "2024-01-02")
    filters = lab.load_component_filters("000300.SSE", "2024-01-01", "2024-01-02")
    lab.add_contract_setting("000001.SSE", long_rate=0.001, short_rate=0.002, size=100, pricetick=0.01)

    assert [dt.strftime("%Y-%m-%d") for dt in loaded] == ["2024-01-01", "2024-01-02"]
    assert sorted(symbols) == ["000001.SSE", "000002.SSE"]
    assert "000001.SSE" in filters
    assert lab.load_contract_setttings()["000001.SSE"]["size"] == 100


def test_alpha_lab_persists_artifacts_with_existing_layout(tmp_path) -> None:
    lab = AlphaLab(str(tmp_path))
    dataset = SimpleNamespace(name="dataset")
    model = SimpleNamespace(name="model")
    signal = pl.DataFrame({"datetime": [datetime(2024, 1, 1)], "vt_symbol": ["000001.SSE"], "score": [1.5]})

    lab.save_dataset("demo", dataset)  # type: ignore[arg-type]
    lab.save_model("demo", model)  # type: ignore[arg-type]
    lab.save_signal("demo", signal)

    assert lab.load_dataset("demo").name == "dataset"  # type: ignore[union-attr]
    assert lab.load_model("demo").name == "model"  # type: ignore[union-attr]
    assert lab.load_signal("demo")["score"].to_list() == [1.5]  # type: ignore[index]
    assert lab.list_all_datasets() == ["demo"]
    assert lab.list_all_models() == ["demo"]
    assert lab.list_all_signals() == ["demo"]

    assert lab.remove_dataset("demo")
    assert lab.remove_model("demo")
    assert lab.remove_signal("demo")
    assert lab.load_dataset("missing") is None
    assert lab.load_model("missing") is None
    assert lab.load_signal("missing") is None
