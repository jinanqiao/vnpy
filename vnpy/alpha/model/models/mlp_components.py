from __future__ import annotations

import copy
from collections import defaultdict
from typing import TYPE_CHECKING, cast

import numpy as np
import pandas as pd
import polars as pl

try:
    import torch
except ImportError:  # pragma: no cover - exercised in dependency-boundary tests
    torch = None  # type: ignore[assignment]

from vnpy.alpha import AlphaDataset, Segment, logger
from vnpy.alpha.optional import require_optional_dependency

if TYPE_CHECKING:
    from .mlp_model import MlpModel


class MlpDatasetAdapter:
    """Convert AlphaDataset frames into tensors used by MLP training/inference."""

    def prepare_train_valid(
        self,
        dataset: AlphaDataset,
        device: str,
        evaluation_results: dict[Segment, list[float]],
    ) -> tuple[dict[str, dict[Segment, torch.Tensor]], list[str]]:
        torch_module = require_torch()
        train_valid_data: dict[str, dict[Segment, torch.Tensor]] = defaultdict(dict)

        for segment in [Segment.TRAIN, Segment.VALID]:
            df: pl.DataFrame = dataset.fetch_learn(segment)
            df = df.sort(["datetime", "vt_symbol"])

            features = df.select(df.columns[2: -1]).to_numpy()
            labels = np.array(df["label"])

            train_valid_data["x"][segment] = torch_module.from_numpy(features).float().to(device)
            train_valid_data["y"][segment] = torch_module.from_numpy(labels).float().to(device)
            evaluation_results[segment] = []

        df = dataset.fetch_learn(Segment.TRAIN)
        feature_names = df.columns[2:-1]

        return train_valid_data, feature_names

    def prepare_infer(self, dataset: AlphaDataset, segment: Segment) -> np.ndarray:
        df: pl.DataFrame = dataset.fetch_infer(segment)
        df = df.sort(["datetime", "vt_symbol"])
        return df.select(df.columns[2: -1]).to_numpy()


def require_torch():
    if torch is not None:
        return torch
    return require_optional_dependency("torch", "MLP model training and prediction")


class MlpTrainingLoop:
    """Orchestrate model training without owning model internals."""

    def __init__(self, owner: MlpModel) -> None:
        self.owner = owner

    def run(
        self,
        train_valid_data: dict[str, dict[Segment, torch.Tensor]],
        evaluation_results: dict[Segment, list[float]],
    ) -> dict[str, torch.Tensor] | None:
        early_stop_count: int = 0
        train_loss: float = 0
        best_valid_score: float = np.inf
        best_params = None
        train_samples: int = train_valid_data["y"][Segment.TRAIN].shape[0]

        for step in range(1, self.owner.n_epochs + 1):
            if early_stop_count >= self.owner.early_stop_rounds:
                logger.info("达到早停条件,训练结束")
                break

            batch_loss = self.owner._train_step(train_valid_data, train_samples)
            train_loss += batch_loss

            if step % self.owner.eval_steps == 0 or step == self.owner.n_epochs:
                early_stop_count, best_valid_score, candidate_params = self.owner._evaluate_step(
                    train_valid_data,
                    evaluation_results,
                    step,
                    train_loss,
                    early_stop_count,
                    best_valid_score
                )
                train_loss = 0

                if candidate_params is not None:
                    best_params = copy.deepcopy(candidate_params)

        return best_params


class MlpBatchPredictor:
    """Run neural network predictions in fixed-size batches."""

    def predict(
        self,
        model: torch.nn.Module,
        data: torch.Tensor,
        device: str,
        batch_size: int = 8096,
        return_cpu: bool = True,
    ) -> np.ndarray | torch.Tensor:
        torch_module = require_torch()
        data = data.to(device)
        predictions: list[torch.Tensor] = []
        model.eval()

        with torch_module.no_grad():
            for i in range(0, len(data), batch_size):
                x: torch.Tensor = data[i: i + batch_size]
                predictions.append(model(x.to(device)).detach().reshape(-1))

        if return_cpu:
            return np.concatenate([pr.cpu().numpy() for pr in predictions])

        return torch_module.cat(predictions, dim=0)


class MlpFeatureDetail:
    """Build feature-importance detail output for trained MLP models."""

    def calculate(
        self,
        model: torch.nn.Module,
        feature_names: list[str],
        input_size: int,
        device: str,
    ) -> pd.DataFrame:
        torch_module = require_torch()
        model.eval()
        importance_dict: dict[str, float] = {}

        test_data = torch_module.randn(1000, input_size).to(device)
        base_pred = model(test_data).detach()

        noise_level = 0.1
        for i, feature_name in enumerate(feature_names):
            perturbed_data = test_data.clone()
            perturbed_data[:, i] += torch_module.randn(1000).to(device) * noise_level

            with torch_module.no_grad():
                new_pred = model(perturbed_data)
                importance = torch_module.std(torch_module.abs(new_pred - base_pred)).item()
                importance_dict[feature_name] = importance

        df: pd.DataFrame = pd.DataFrame({
            "Feature": list(importance_dict.keys()),
            "Importance": list(importance_dict.values())
        })
        df = df.sort_values("Importance", ascending=False)
        return cast(pd.DataFrame, df.set_index("Feature"))
