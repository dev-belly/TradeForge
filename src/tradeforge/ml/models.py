"""Fill-probability models: baselines first, then two linear models.

The ordering is a research rule, not a style preference. A non-linear model may
only be reported after the trivial baselines have been measured on the same
split, because a 0.72 AUC means nothing until you know the base rate was 0.68.

Every model here is deliberately modest. The point of this layer is to establish
whether microstructure features carry *any* fill-probability signal on this
data, and to do it without a leakage accident. It is not a trading strategy.

Standardisation is fitted on the training split only and reused unchanged on
validation and test. Fitting it on the whole dataset is one of the most common
silent leaks in applied ML.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np


class FillProbabilityModel(Protocol):
    """Uniform interface so every model is scored by identical code."""

    @property
    def name(self) -> str: ...

    def fit(self, features: np.ndarray, labels: np.ndarray) -> None: ...

    def predict_proba(self, features: np.ndarray) -> np.ndarray: ...


@dataclass
class Standardizer:
    """Column-wise z-scoring, fitted on train and then frozen."""

    mean_: np.ndarray | None = None
    scale_: np.ndarray | None = None
    _n_features: int = 0

    def fit(self, features: np.ndarray) -> Standardizer:
        self._n_features = features.shape[1]
        self.mean_ = np.nanmean(features, axis=0)
        scale = np.nanstd(features, axis=0)
        # A constant column has zero scale; leaving it at 0 would divide by zero
        # and produce inf/NaN silently.
        scale[scale == 0] = 1.0
        self.scale_ = scale
        return self

    def transform(self, features: np.ndarray) -> np.ndarray:
        if self.mean_ is None or self.scale_ is None:
            raise RuntimeError("Standardizer.transform called before fit")
        if features.shape[1] != self._n_features:
            raise ValueError(f"expected {self._n_features} features, got {features.shape[1]}")
        # NaN columns (features that were unavailable at that instant) are
        # imputed with the TRAIN mean, then scaled. Imputing with the test mean
        # would leak.
        filled = np.where(np.isnan(features), self.mean_, features)
        return (filled - self.mean_) / self.scale_

    def fit_transform(self, features: np.ndarray) -> np.ndarray:
        return self.fit(features).transform(features)


def impute_with_train_mean(
    train: np.ndarray, other: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Replace NaN with the train column mean; returns (train, other, means)."""
    means = np.nanmean(train, axis=0)
    means = np.where(np.isnan(means), 0.0, means)
    return (
        np.where(np.isnan(train), means, train),
        np.where(np.isnan(other), means, other),
        means,
    )


# --------------------------------------------------------------------- models


class BaseRateModel:
    """Predicts the training base rate for every sample.

    The model every other model must beat. If nothing beats it, the honest
    conclusion is that the features carry no signal on this data.
    """

    def __init__(self) -> None:
        self._rate = 0.0

    @property
    def name(self) -> str:
        return "base_rate"

    @property
    def is_label_fitted(self) -> bool:
        """Whether shuffling training labels can change this model's ranking."""
        return True

    def fit(self, features: np.ndarray, labels: np.ndarray) -> None:
        del features
        self._rate = float(np.mean(labels)) if len(labels) else 0.0

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        return np.full(features.shape[0], self._rate, dtype=float)


class MidModel:
    """Naive single-feature baseline: the mid price level, standardised.

    Deliberately weak, and included for a specific reason. A raw price level is
    non-stationary, so it cannot generalise across a session. If this model
    scores well, the split is broken - which makes it a useful canary as well as
    a baseline.
    """

    def __init__(self, feature_index: int) -> None:
        self._index = feature_index
        self._model = _logistic()
        self._scaler = Standardizer()

    @property
    def name(self) -> str:
        return "mid"

    @property
    def is_label_fitted(self) -> bool:
        """Whether shuffling training labels can change this model's ranking."""
        return True

    def fit(self, features: np.ndarray, labels: np.ndarray) -> None:
        column = features[:, [self._index]]
        scaled = self._scaler.fit_transform(column)
        self._model.fit(scaled, labels)

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        column = features[:, [self._index]]
        return _probabilities(self._model, self._scaler.transform(column))


class ImbalanceThresholdModel:
    """Rule: fill is more likely when the book leans against our order.

    A one-parameter decision rule, not a fitted model. It is here because order
    book imbalance is the single most-cited predictor of short-horizon price
    moves, and any fitted model must be compared against the obvious rule.
    """

    def __init__(self, feature_index: int, *, threshold: float = 0.0, invert: bool = True) -> None:
        self._index = feature_index
        self._threshold = threshold
        self._invert = invert
        self._rate = 0.0

    @property
    def name(self) -> str:
        return "imbalance_threshold"

    @property
    def is_label_fitted(self) -> bool:
        """Whether shuffling training labels can change this model's ranking."""
        return False

    def fit(self, features: np.ndarray, labels: np.ndarray) -> None:
        del features
        self._rate = float(np.mean(labels)) if len(labels) else 0.0

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        column = features[:, self._index]
        above = column > self._threshold
        signal = ~above if self._invert else above
        return np.where(signal, min(1.0, self._rate * 2.0), 0.0)


class LogisticRegressionModel:
    """L2-regularised logistic regression on the full feature vector."""

    def __init__(self, *, max_iter: int = 1000, c: float = 1.0, seed: int = 0) -> None:
        self._model = _logistic(max_iter=max_iter, c=c, seed=seed)
        self._scaler = Standardizer()

    @property
    def name(self) -> str:
        return "logistic_regression"

    @property
    def is_label_fitted(self) -> bool:
        """Whether shuffling training labels can change this model's ranking."""
        return True

    def fit(self, features: np.ndarray, labels: np.ndarray) -> None:
        if len(np.unique(labels)) < 2:
            raise ValueError(
                "training labels are single-class; a classifier cannot be fitted "
                "and reporting an AUC would be meaningless"
            )
        self._scaler.fit(features)
        self._model.fit(self._scaler.transform(features), labels)

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        return _probabilities(self._model, self._scaler.transform(features))


class RidgeModel:
    """Ridge regression on the 0/1 label, clipped to [0, 1].

    A linear probability model. It is included because it is robust to the
    feature scaling problems that break logistic regression on wide, correlated
    microstructure features, and because agreement between the two is weak
    evidence that neither is an artefact of the solver.
    """

    def __init__(self, *, alpha: float = 1.0) -> None:
        from sklearn.linear_model import Ridge

        self._model = Ridge(alpha=alpha)
        self._scaler = Standardizer()

    @property
    def name(self) -> str:
        return "ridge"

    @property
    def is_label_fitted(self) -> bool:
        """Whether shuffling training labels can change this model's ranking."""
        return True

    def fit(self, features: np.ndarray, labels: np.ndarray) -> None:
        self._scaler.fit(features)
        self._model.fit(self._scaler.transform(features), labels.astype(float))

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        raw = self._model.predict(self._scaler.transform(features))
        return np.clip(raw, 0.0, 1.0)


# ------------------------------------------------------------------- helpers


def _logistic(max_iter: int = 1000, c: float = 1.0, seed: int = 0) -> object:
    from sklearn.linear_model import LogisticRegression

    return LogisticRegression(max_iter=max_iter, C=c, random_state=seed)


def _probabilities(model: object, features: np.ndarray) -> np.ndarray:
    proba = model.predict_proba(features)  # type: ignore[attr-defined]
    return np.asarray(proba)[:, 1]


def build_models(feature_names: Sequence[str], *, seed: int = 23) -> list[FillProbabilityModel]:
    """All four baselines/models, in reporting order (simplest first)."""
    names = list(feature_names)
    mid_index = names.index("mid_ticks") if "mid_ticks" in names else 0
    imbalance_index = names.index("top_imbalance") if "top_imbalance" in names else 0
    return [
        BaseRateModel(),
        MidModel(mid_index),
        ImbalanceThresholdModel(imbalance_index),
        LogisticRegressionModel(seed=seed),
        RidgeModel(),
    ]
