"""Model evaluation with a leakage canary.

Two things every report here does:

  1. Prints the **base rate** next to every accuracy. An accuracy of 0.70 on a
     target whose base rate is 0.68 is a failure reported as a success.
  2. Runs a **label-shuffle canary**. Refit the identical pipeline on randomly
     permuted labels; a leak-free pipeline must collapse to AUC ~0.5. If the
     canary scores above chance, the reported numbers are not trustworthy and
     the evaluation says so instead of printing them.

AUC is computed without scikit-learn's `roc_auc_score` so the implementation is
visible: the Mann-Whitney U statistic with average ranks for ties.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from .models import FillProbabilityModel

#: How many null standard errors of AUC count as "above chance".
CANARY_Z = 2.5
#: Floor, so a huge sample cannot make the canary hypersensitive to rounding.
MIN_CANARY_TOLERANCE = 0.05


def null_auc_standard_error(n_positive: int, n_negative: int) -> float | None:
    """Standard error of AUC under the null hypothesis of no association.

    Uses the Hanley-McNeil variance for the Mann-Whitney statistic. This exists
    because a *fixed* tolerance is not a statistical statement: with 16 negative
    examples, an AUC of 0.60 is about one standard error from chance, while with
    5000 examples the same value would be a real finding. Comparing against a
    null-derived tolerance is the difference between a test and a vibe.
    """
    if n_positive <= 0 or n_negative <= 0:
        return None
    n = n_positive + n_negative
    return float(np.sqrt((n + 1) / (12.0 * n_positive * n_negative)))


def canary_tolerance(labels: Sequence[int] | np.ndarray) -> float:
    """Tolerance for the leakage canary, derived from the sample at hand."""
    y = np.asarray(labels)
    se = null_auc_standard_error(int(np.sum(y == 1)), int(np.sum(y == 0)))
    if se is None:
        return MIN_CANARY_TOLERANCE
    return max(CANARY_Z * se, MIN_CANARY_TOLERANCE)


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    predicted_mean: float
    observed_rate: float
    n: int

    def to_dict(self) -> dict[str, object]:
        return {
            "predicted_mean": self.predicted_mean,
            "observed_rate": self.observed_rate,
            "n": self.n,
        }


@dataclass(frozen=True, slots=True)
class ClassificationReport:
    """Metrics for one model on one split."""

    model: str
    split: str
    n: int
    base_rate: float
    accuracy: float
    auc: float | None
    brier: float
    precision: float
    recall: float
    f1: float
    lift_over_base_rate: float
    #: Paired standard error of the lift, and its 95% interval. The lift is a
    #: *paired* difference: on the same samples, model accuracy minus the
    #: accuracy of the always-predict-positive rule. Reporting only the point
    #: estimate invites the reader to treat a fraction of a percentage point as
    #: a finding, which at this sample size it is not.
    lift_standard_error: float = 0.0
    lift_ci_lower: float = 0.0
    lift_ci_upper: float = 0.0
    calibration: tuple[CalibrationBin, ...] = ()

    @property
    def beats_base_rate(self) -> bool:
        """Whether the point estimate exceeds the base rate. Rarely the question.

        Kept because it is a factual comparison, but it is not evidence of
        anything on its own - see `lift_is_distinguishable`.
        """
        return self.accuracy > self.base_rate

    @property
    def lift_is_distinguishable(self) -> bool:
        """Whether the 95% interval for the lift excludes zero.

        This is the honest form of "does the model beat the base rate". On the
        bundled data it is False for every model, which is the finding.
        """
        return self.lift_ci_lower > 0.0 or self.lift_ci_upper < 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "split": self.split,
            "n": self.n,
            "base_rate": self.base_rate,
            "accuracy": self.accuracy,
            "auc": self.auc,
            "brier": self.brier,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "lift_over_base_rate": self.lift_over_base_rate,
            "lift_standard_error": self.lift_standard_error,
            "lift_ci_lower": self.lift_ci_lower,
            "lift_ci_upper": self.lift_ci_upper,
            "beats_base_rate": self.beats_base_rate,
            "lift_is_distinguishable": self.lift_is_distinguishable,
            "calibration": [b.to_dict() for b in self.calibration],
        }


@dataclass(frozen=True, slots=True)
class LeakageCanary:
    """Result of refitting on shuffled labels."""

    model: str
    split: str
    auc_on_shuffled_labels: float | None
    accuracy_on_shuffled_labels: float
    tolerance: float
    passed: bool
    seed: int
    applicable: bool = True

    @property
    def verdict(self) -> str:
        if not self.applicable:
            return (
                "not applicable: this model is a fixed decision rule, so shuffling "
                "training labels cannot change its ranking"
            )
        if self.auc_on_shuffled_labels is None:
            return "not applicable (single-class or degenerate sample)"
        if self.passed:
            return (
                f"passed: shuffled-label AUC {self.auc_on_shuffled_labels:.4f} is "
                f"within {self.tolerance:.4f} of chance"
            )
        return (
            "FAILED: the pipeline recovered signal from random labels, so the "
            "reported metrics are not trustworthy"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "split": self.split,
            "auc_on_shuffled_labels": self.auc_on_shuffled_labels,
            "accuracy_on_shuffled_labels": self.accuracy_on_shuffled_labels,
            "tolerance": self.tolerance,
            "passed": self.passed,
            "applicable": self.applicable,
            "seed": self.seed,
            "verdict": self.verdict,
        }


def roc_auc(
    labels: Sequence[int] | np.ndarray, scores: Sequence[float] | np.ndarray
) -> float | None:
    """AUC via the Mann-Whitney U statistic, with average ranks for ties."""
    y = np.asarray(labels, dtype=float)
    s = np.asarray(scores, dtype=float)
    n_pos = int(np.sum(y == 1))
    n_neg = int(np.sum(y == 0))
    if n_pos == 0 or n_neg == 0:
        return None
    ranks = _average_ranks(s)
    rank_sum_pos = float(np.sum(ranks[y == 1]))
    return (rank_sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def _average_ranks(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    sorted_values = values[order]
    i = 0
    while i < len(values):
        j = i
        while j + 1 < len(values) and sorted_values[j + 1] == sorted_values[i]:
            j += 1
        average = (i + j) / 2.0 + 1.0
        ranks[order[i : j + 1]] = average
        i = j + 1
    return ranks


def evaluate(
    model: FillProbabilityModel,
    features: np.ndarray,
    labels: np.ndarray,
    *,
    split: str,
    threshold: float = 0.5,
    calibration_bins: int = 10,
) -> ClassificationReport:
    """Score a fitted model. Assumes `model` was fitted on a different split."""
    probabilities = np.asarray(model.predict_proba(features), dtype=float)
    y = np.asarray(labels, dtype=float)
    predictions = (probabilities >= threshold).astype(float)

    base_rate = float(np.mean(y)) if len(y) else 0.0
    accuracy = float(np.mean(predictions == y)) if len(y) else 0.0
    tp = float(np.sum((predictions == 1) & (y == 1)))
    fp = float(np.sum((predictions == 1) & (y == 0)))
    fn = float(np.sum((predictions == 0) & (y == 1)))
    precision = tp / (tp + fp) if tp + fp > 0 else 0.0
    recall = tp / (tp + fn) if tp + fn > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else 0.0
    brier = float(np.mean((probabilities - y) ** 2)) if len(y) else 0.0

    lift_se, lift_low, lift_high = _paired_lift_interval(predictions, y, confidence=0.95)

    return ClassificationReport(
        model=model.name,
        split=split,
        n=len(y),
        base_rate=base_rate,
        accuracy=accuracy,
        auc=roc_auc(labels, probabilities),
        brier=brier,
        precision=precision,
        recall=recall,
        f1=f1,
        lift_over_base_rate=accuracy - base_rate,
        lift_standard_error=lift_se,
        lift_ci_lower=lift_low,
        lift_ci_upper=lift_high,
        calibration=_calibration(probabilities, y, calibration_bins),
    )


def _paired_lift_interval(
    predictions: np.ndarray, labels: np.ndarray, *, confidence: float
) -> tuple[float, float, float]:
    """Standard error and interval for `accuracy - base_rate`, paired by sample.

    The two accuracies come from the *same* observations, so the difference is a
    paired quantity: per sample, `d_i = correct_i - (y_i == 1)`, because the
    base-rate rule predicts positive for every sample. The mean of `d` is the
    lift and `sd(d)/sqrt(n)` is its standard error.

    Treating the two accuracies as independent proportions would overstate the
    standard error, because both are computed on the same labels. There is a
    test for exactly that.

    Returns `(standard_error, lower, upper)`, or zeros when the sample is too
    small for a standard error to mean anything.
    """
    n = len(labels)
    if n < 2:
        return 0.0, 0.0, 0.0
    correct = (predictions == labels).astype(float)
    base_correct = (labels == 1).astype(float)
    differences = correct - base_correct
    se = float(np.std(differences, ddof=1) / np.sqrt(n))
    lift = float(np.mean(differences))
    z = 1.959963984540054 if confidence == 0.95 else float(_z_for(confidence))
    return se, lift - z * se, lift + z * se


def _z_for(confidence: float) -> float:
    """Two-sided normal critical value, from a small table.

    Deliberately not a general inverse-normal implementation: only the levels
    this module uses are supported, and an unsupported level should be an
    obvious error rather than a quietly wrong number.
    """
    table = {0.80: 1.2815515655446004, 0.90: 1.6448536269514722, 0.95: 1.959963984540054}
    try:
        return table[confidence]
    except KeyError as exc:
        raise ValueError(
            f"confidence {confidence} is not tabulated; supported: {sorted(table)}"
        ) from exc


def _calibration(
    probabilities: np.ndarray, labels: np.ndarray, bins: int
) -> tuple[CalibrationBin, ...]:
    if len(probabilities) == 0:
        return ()
    edges = np.linspace(0.0, 1.0, bins + 1)
    out: list[CalibrationBin] = []
    for index in range(bins):
        low, high = edges[index], edges[index + 1]
        mask = (probabilities >= low) & (
            probabilities < high if index < bins - 1 else probabilities <= high
        )
        count = int(np.sum(mask))
        if count == 0:
            continue
        out.append(
            CalibrationBin(
                predicted_mean=float(np.mean(probabilities[mask])),
                observed_rate=float(np.mean(labels[mask])),
                n=count,
            )
        )
    return tuple(out)


@dataclass(frozen=True, slots=True)
class FeatureLeakFinding:
    """One feature's univariate association with the label, on the test split."""

    feature: str
    auc: float | None
    tolerance: float
    suspicious: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "feature": self.feature,
            "auc": self.auc,
            "tolerance": self.tolerance,
            "suspicious": self.suspicious,
            "note": (
                "a single raw feature that ranks a forward-looking label this "
                "well is almost always a leak, not an edge"
                if self.suspicious
                else ""
            ),
        }


def feature_leak_screen(
    x_test: np.ndarray,
    y_test: np.ndarray,
    feature_names: Sequence[str],
    *,
    max_auc: float = 0.90,
) -> tuple[FeatureLeakFinding, ...]:
    """Screen every raw feature for a suspiciously strong label association.

    This is the check that actually catches the most common leak in this kind of
    project: a feature computed over a window that overlaps the label horizon,
    or a column derived from the target. It is deliberately *univariate* - a
    linear model can dilute a leaky feature across correlated columns, while the
    raw column cannot hide.

    `max_auc` is a fixed bar rather than a null-derived one because the claim
    being tested is absolute: no single microstructure feature should rank a
    30-second-forward fill label at 0.9 AUC. A model can be weak; a feature that
    strong is a bug.
    """
    findings: list[FeatureLeakFinding] = []
    tolerance = canary_tolerance(y_test)
    for index, name in enumerate(feature_names):
        if index >= x_test.shape[1]:
            break
        column = x_test[:, index]
        if np.all(np.isnan(column)):
            findings.append(
                FeatureLeakFinding(feature=name, auc=None, tolerance=tolerance, suspicious=False)
            )
            continue
        filled = np.where(np.isnan(column), np.nanmean(column), column)
        auc = roc_auc(y_test, filled)
        suspicious = auc is not None and max(auc, 1.0 - auc) >= max_auc
        findings.append(
            FeatureLeakFinding(feature=name, auc=auc, tolerance=tolerance, suspicious=suspicious)
        )
    return tuple(findings)


def leakage_canary(
    model_factory: Callable[[], FillProbabilityModel],
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_test: np.ndarray,
    y_test: np.ndarray,
    *,
    split: str,
    seed: int = 23,
) -> LeakageCanary:
    """Break the train-time feature/label link and check test AUC collapses.

    Procedure: shuffle the **training** labels, fit, then score on the **real**
    test labels. A model whose predictions still rank the true test labels has
    learned something that does not come from the training labels.

    **Scope - this canary is narrower than it looks.** It detects two things:

      * a fixed decision rule presented as a fitted model (shuffling labels
        cannot change a rule that ignores them);
      * a pipeline where preprocessing carries label information from train to
        test - a scaler or target encoder fitted on the full dataset.

    It does **not** detect "one raw feature encodes the label": a linear model
    fitted to noise shrinks its coefficients toward zero and collapses to the
    base rate, so the canary passes while the real metrics are inflated. That
    leak class is caught by `feature_leak_screen`, which tests each raw column
    directly. Both checks run in the pipeline; neither replaces the other.

    Two design details that matter:

      * The canary must hold out data. Fitting and scoring on the same rows
        measures overfitting, not leakage: a 21-feature linear model on 106
        samples reaches AUC ~0.75 in-sample even on random labels, which would
        be reported as a false alarm.
      * The model factory is called fresh so no fitted scaler or coefficient
        vector can carry over from the real run.
    """
    rng = np.random.default_rng(seed)
    shuffled_train = np.array(y_train, dtype=int)
    rng.shuffle(shuffled_train)

    model = model_factory()
    tolerance = canary_tolerance(y_test)

    if not getattr(model, "is_label_fitted", True):
        # A fixed rule (an imbalance threshold, say) ranks identically whatever
        # the training labels are. Reporting a "failure" here would be a false
        # alarm caused by applying the wrong test.
        return LeakageCanary(
            model=model.name,
            split=split,
            auc_on_shuffled_labels=None,
            accuracy_on_shuffled_labels=0.0,
            tolerance=tolerance,
            passed=True,
            seed=seed,
            applicable=False,
        )
    if len(np.unique(shuffled_train)) < 2 or len(np.unique(y_test)) < 2:
        return LeakageCanary(
            model=model.name,
            split=split,
            auc_on_shuffled_labels=None,
            accuracy_on_shuffled_labels=0.0,
            tolerance=tolerance,
            passed=True,
            seed=seed,
            applicable=False,
        )

    model.fit(x_train, shuffled_train)
    report = evaluate(model, x_test, y_test, split=f"{split}:shuffled_train_labels")
    auc = report.auc
    return LeakageCanary(
        model=model.name,
        split=split,
        auc_on_shuffled_labels=auc,
        accuracy_on_shuffled_labels=report.accuracy,
        tolerance=tolerance,
        passed=auc is None or abs(auc - 0.5) <= tolerance,
        seed=seed,
        applicable=True,
    )
