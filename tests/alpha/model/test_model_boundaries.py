from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from vnpy.alpha.dataset import Segment
from vnpy.alpha.model.models.lasso_model import LassoModel
from vnpy.alpha.optional import OptionalDependencyError, optional_dependency_message


class ToyDataset:
    def __init__(self) -> None:
        self.learn_df = pl.DataFrame({
            "datetime": [
                "2024-01-01",
                "2024-01-01",
                "2024-01-02",
                "2024-01-02",
            ],
            "vt_symbol": ["A", "B", "A", "B"],
            "factor_a": [1.0, 2.0, 3.0, 4.0],
            "factor_b": [4.0, 3.0, 2.0, 1.0],
            "label": [0.1, 0.2, 0.3, 0.4],
        })
        self.infer_df = self.learn_df

    def fetch_learn(self, segment: Segment) -> pl.DataFrame:
        return self.learn_df

    def fetch_infer(self, segment: Segment) -> pl.DataFrame:
        return self.infer_df


def test_lasso_model_contract() -> None:
    dataset = ToyDataset()
    model = LassoModel(alpha=0.0001, max_iter=1000, random_state=7)

    with pytest.raises(ValueError, match="not fitted"):
        model.predict(dataset, Segment.TEST)  # type: ignore[arg-type]

    model.fit(dataset)  # type: ignore[arg-type]
    predictions = model.predict(dataset, Segment.TEST)  # type: ignore[arg-type]

    assert predictions.shape == (4,)
    assert np.isfinite(predictions).all()
    assert model.feature_names == ["factor_a", "factor_b"]


def test_lightgbm_model_import_is_optional() -> None:
    from vnpy.alpha.model.models.lgb_model import LgbModel
    from vnpy.alpha.model.models import lgb_model

    if lgb_model.lgb is None:
        with pytest.raises(OptionalDependencyError, match="lightgbm"):
            LgbModel(num_boost_round=1, early_stopping_rounds=1, seed=7)
        return

    model = LgbModel(num_boost_round=1, early_stopping_rounds=1, seed=7)
    assert model.model is None


def test_mlp_model_import_is_optional() -> None:
    from vnpy.alpha.model.models.mlp_model import MlpModel
    from vnpy.alpha.model.models import mlp_model

    if mlp_model.torch is None:
        with pytest.raises(OptionalDependencyError, match="torch"):
            MlpModel(input_size=2, hidden_sizes=(4,))
        return

    model = MlpModel(input_size=2, hidden_sizes=(4,), n_epochs=1, seed=7)
    assert model.fitted is False


def test_optional_dependency_message_is_actionable() -> None:
    message = optional_dependency_message("torch", "MLP model training")

    assert "torch" in message
    assert "MLP model training" in message
    assert "alpha" in message


def test_mlp_components_prepare_and_predict() -> None:
    torch = pytest.importorskip("torch")

    from vnpy.alpha.model.models.mlp_components import MlpBatchPredictor, MlpDatasetAdapter
    from vnpy.alpha.model.models.mlp_model import MlpNetwork

    dataset = ToyDataset()
    evaluation_results: dict[Segment, list[float]] = {}
    adapter = MlpDatasetAdapter()

    train_valid_data, feature_names = adapter.prepare_train_valid(
        dataset,  # type: ignore[arg-type]
        "cpu",
        evaluation_results,
    )

    assert feature_names == ["factor_a", "factor_b"]
    assert train_valid_data["x"][Segment.TRAIN].shape == (4, 2)
    assert evaluation_results[Segment.TRAIN] == []
    assert evaluation_results[Segment.VALID] == []

    network = MlpNetwork(input_size=2, hidden_sizes=(4,))
    predictor = MlpBatchPredictor()
    predictions = predictor.predict(
        network,
        torch.Tensor([[1.0, 2.0], [3.0, 4.0]]),
        "cpu",
    )

    assert predictions.shape == (2,)
