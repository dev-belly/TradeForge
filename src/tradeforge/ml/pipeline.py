"""Fill-probability pipeline: dataset -> purged split -> models -> reports.

The order of operations is the leak-control design, so it is spelled out:

    1. assert samples are time ordered        (split.py)
    2. split by index, purge at each boundary (split.py)
    3. fit the scaler on TRAIN only           (models.py)
    4. fit each model on TRAIN, score on VALIDATION
    5. refit on TRAIN + VALIDATION, score on TEST once
    6. run the label-shuffle canary on TEST

Step 5 exists so the test set is touched exactly once per model. Re-selecting a
model because of its test score is how a test set stops being a test set.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from .dataset import Sample, feature_matrix
from .evaluate import (
    ClassificationReport,
    FeatureLeakFinding,
    LeakageCanary,
    evaluate,
    feature_leak_screen,
    leakage_canary,
)
from .models import build_models
from .split import (
    PurgedSplit,
    assert_disjoint,
    assert_splits_usable,
    assert_time_ordered,
    purged_time_split,
    required_embargo_samples,
)


@dataclass(frozen=True, slots=True)
class MlConfig:
    """Everything that shapes the experiment, all from `configs/research.yaml`."""

    train_frac: float = 0.6
    validation_frac: float = 0.2
    test_frac: float = 0.2
    #: Purge margin at each boundary, in nanoseconds. Must be >= the label
    #: horizon; the effective purge is the larger of the two.
    embargo_ns: int = 60_000_000_000
    seed: int = 23
    horizon_ns: int = 30_000_000_000
    sample_interval_ns: int = 5_000_000_000

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> MlConfig:
        ml = payload.get("ml", {}) if isinstance(payload, dict) else {}
        if not isinstance(ml, dict):
            raise TypeError("research config must contain an 'ml' mapping")
        split = ml.get("split", {}) if isinstance(ml.get("split"), dict) else {}
        return cls(
            train_frac=float(str(split.get("train_frac", 0.6))),
            validation_frac=float(str(split.get("validation_frac", 0.2))),
            test_frac=float(str(split.get("test_frac", 0.2))),
            embargo_ns=int(str(split.get("embargo_ns", 60_000_000_000))),
            seed=int(str(ml.get("seed", 23))),
            horizon_ns=int(str(ml.get("horizon_ns", 30_000_000_000))),
            sample_interval_ns=int(str(ml.get("sample_interval_ns", 5_000_000_000))),
        )


@dataclass(frozen=True, slots=True)
class MlExperimentResult:
    """Reports plus the split and dataset description they were produced under."""

    dataset: dict[str, Any]
    split: PurgedSplit
    feature_names: tuple[str, ...]
    validation_reports: tuple[ClassificationReport, ...]
    test_reports: tuple[ClassificationReport, ...]
    canaries: tuple[LeakageCanary, ...]
    feature_screen: tuple[FeatureLeakFinding, ...]
    seed: int
    notes: tuple[str, ...] = ()

    @property
    def any_canary_failed(self) -> bool:
        return any(not c.passed for c in self.canaries)

    @property
    def suspicious_features(self) -> tuple[str, ...]:
        return tuple(f.feature for f in self.feature_screen if f.suspicious)

    @property
    def any_leak_detected(self) -> bool:
        return self.any_canary_failed or bool(self.suspicious_features)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": dict(self.dataset),
            "split": self.split.to_dict(),
            "feature_names": list(self.feature_names),
            "validation_reports": [r.to_dict() for r in self.validation_reports],
            "test_reports": [r.to_dict() for r in self.test_reports],
            "canaries": [c.to_dict() for c in self.canaries],
            "feature_screen": [f.to_dict() for f in self.feature_screen],
            "suspicious_features": list(self.suspicious_features),
            "any_canary_failed": self.any_canary_failed,
            "any_leak_detected": self.any_leak_detected,
            "seed": self.seed,
            "notes": list(self.notes),
        }


DEFAULT_NOTES: tuple[str, ...] = (
    "Synthetic data only. These numbers describe whether microstructure features "
    "carry fill-probability signal on a deterministic generator.",
    "The label assumes cancels sit BEHIND our hypothetical order; L2 data cannot "
    "distinguish cancels ahead from cancels behind.",
    "Test is scored once per model, after the model was chosen on validation.",
)


def run_fill_probability_experiment(
    samples: Sequence[Sample],
    *,
    config: MlConfig,
    dataset_report: dict[str, Any] | None = None,
) -> MlExperimentResult:
    """Fit every baseline on a purged time split and score it once on test."""
    if len(samples) < 20:
        raise ValueError(f"need at least 20 samples to split and evaluate, got {len(samples)}")
    timestamps = [s.timestamp_ns for s in samples]
    assert_time_ordered(timestamps)

    embargo = required_embargo_samples(
        horizon_ns=config.horizon_ns,
        sample_interval_ns=config.sample_interval_ns,
        configured_embargo_ns=config.embargo_ns,
    )
    split = purged_time_split(
        len(samples),
        train_frac=config.train_frac,
        validation_frac=config.validation_frac,
        test_frac=config.test_frac,
        embargo_samples=embargo,
    )
    assert_disjoint(split)
    assert_splits_usable(split)

    rows, labels = feature_matrix(samples)
    features = np.asarray(rows, dtype=float)
    targets = np.asarray(labels, dtype=int)
    names = tuple(_feature_names())

    x_train = features[list(split.train)]
    y_train = targets[list(split.train)]
    x_val = features[list(split.validation)]
    y_val = targets[list(split.validation)]
    x_test = features[list(split.test)]
    y_test = targets[list(split.test)]

    validation_reports: list[ClassificationReport] = []
    test_reports: list[ClassificationReport] = []
    canaries: list[LeakageCanary] = []
    notes = list(DEFAULT_NOTES)

    for index, _ in enumerate(build_models(names, seed=config.seed)):
        factory = lambda i=index: build_models(names, seed=config.seed)[i]  # noqa: E731
        model = factory()
        model.fit(x_train, y_train)
        validation_reports.append(evaluate(model, x_val, y_val, split="validation"))

        combined = np.vstack([x_train, x_val])
        combined_labels = np.concatenate([y_train, y_val])
        final = factory()
        final.fit(combined, combined_labels)
        test_reports.append(evaluate(final, x_test, y_test, split="test"))

        if model.name != "base_rate":
            canaries.append(
                leakage_canary(
                    factory,
                    x_train,
                    y_train,
                    x_test,
                    y_test,
                    split="test",
                    seed=config.seed,
                )
            )

    if not np.any(y_train == 1) or not np.any(y_train == 0):
        notes.append(
            "The training split is single-class; classification metrics are not "
            "interpretable and the AUC column is intentionally blank."
        )
    screen = feature_leak_screen(x_test, y_test, names)
    suspicious = [f.feature for f in screen if f.suspicious]
    if suspicious:
        notes.append(
            f"Feature screen flagged {suspicious}: a single raw column ranks the "
            "forward-looking label above 0.90 AUC. Treat this as a leak until "
            "proven otherwise, and do not quote the metrics above."
        )
    if any(c.passed is False for c in canaries):
        notes.append(
            "At least one leakage canary failed. Treat every metric in this "
            "report as unverified until the pipeline is fixed."
        )

    return MlExperimentResult(
        dataset=dict(dataset_report or {}),
        split=split,
        feature_names=names,
        validation_reports=tuple(validation_reports),
        test_reports=tuple(test_reports),
        canaries=tuple(canaries),
        feature_screen=screen,
        seed=config.seed,
        notes=tuple(notes),
    )


def _feature_names() -> tuple[str, ...]:
    from ..microstructure.features import FeatureSet

    return FeatureSet.numeric_fields()
