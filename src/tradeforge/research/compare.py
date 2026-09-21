"""Paired strategy comparison.

The pairing is the point. Two strategies run on the *same* synthetic session
(identical seed, identical event stream) see the same market, so the difference
between them is not contaminated by market variance. An unpaired comparison of
two 30-minute samples would need an order of magnitude more data to say the same
thing, and would still be confounded by whichever sample happened to be calmer.

Multiple comparisons are corrected with Holm-Bonferroni. The correction is
applied by the caller over the whole family of tests in one experiment, and the
corrected p-values are what get reported - not the raw ones.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean, stdev

from .bootstrap import ConfidenceInterval, block_bootstrap_ci


@dataclass(frozen=True, slots=True)
class PairedComparison:
    """Difference between two strategies, paired by observation."""

    label_a: str
    label_b: str
    metric: str
    n_pairs: int

    mean_a: float
    mean_b: float
    mean_difference: float

    ci: ConfidenceInterval
    wins_a: int
    wins_b: int
    ties: int
    sign_test_p: float
    holm_adjusted_p: float | None
    effect_size: float | None
    #: True when the CI excludes zero. NOT a substitute for judgement.
    significant: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "label_a": self.label_a,
            "label_b": self.label_b,
            "metric": self.metric,
            "n_pairs": self.n_pairs,
            "mean_a": self.mean_a,
            "mean_b": self.mean_b,
            "mean_difference": self.mean_difference,
            "ci_lower": self.ci.lower,
            "ci_upper": self.ci.upper,
            "confidence": self.ci.confidence,
            "wins_a": self.wins_a,
            "wins_b": self.wins_b,
            "ties": self.ties,
            "sign_test_p": self.sign_test_p,
            "holm_adjusted_p": self.holm_adjusted_p,
            "effect_size": self.effect_size,
            "significant": self.significant,
            "note": (
                "difference is a - b; a negative value means a is cheaper. "
                "Significance here means the interval excludes zero, nothing more."
            ),
        }


def paired_comparison(
    label_a: str,
    label_b: str,
    values_a: Sequence[float],
    values_b: Sequence[float],
    *,
    metric: str,
    block_length: int = 50,
    n_resamples: int = 2000,
    confidence: float = 0.95,
    seed: int = 11,
) -> PairedComparison:
    """Compare two strategies on identical observations, paired index by index."""
    a = [float(v) for v in values_a]
    b = [float(v) for v in values_b]
    if len(a) != len(b):
        raise ValueError(
            f"paired comparison needs equal-length samples: {len(a)} vs {len(b)}. "
            "Pair by seed, not by convenience."
        )
    if not a:
        raise ValueError("paired comparison needs at least one pair")

    differences = [x - y for x, y in zip(a, b, strict=True)]
    ci = block_bootstrap_ci(
        differences,
        block_length=block_length,
        n_resamples=n_resamples,
        confidence=confidence,
        seed=seed,
    )
    wins_a = sum(1 for d in differences if d < 0)
    wins_b = sum(1 for d in differences if d > 0)
    ties = len(differences) - wins_a - wins_b

    return PairedComparison(
        label_a=label_a,
        label_b=label_b,
        metric=metric,
        n_pairs=len(a),
        mean_a=fmean(a),
        mean_b=fmean(b),
        mean_difference=fmean(differences),
        ci=ci,
        wins_a=wins_a,
        wins_b=wins_b,
        ties=ties,
        sign_test_p=sign_test_p_value(wins_a, wins_b),
        holm_adjusted_p=None,
        effect_size=_paired_effect_size(differences),
        significant=ci.excludes_zero,
    )


def _paired_effect_size(differences: Sequence[float]) -> float | None:
    """Cohen's d for paired samples. None when the difference is degenerate."""
    if len(differences) < 2:
        return None
    spread = stdev(differences)
    if spread == 0:
        return None
    return fmean(differences) / spread


def sign_test_p_value(wins: int, losses: int) -> float:
    """Exact two-sided binomial sign test on the non-tied pairs.

    Used instead of a t-test because execution costs are heavy-tailed and
    bounded by nothing: a t-test on 30 paired observations would be a
    distributional assumption we cannot defend.
    """
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2.0 * tail)


def holm_bonferroni(p_values: Sequence[float]) -> list[float]:
    """Holm-Bonferroni step-down adjustment over one family of tests.

    Reported alongside the raw p-values so a reader can see the cost of
    searching: with eight comparisons at alpha=0.05, one "significant" result is
    what you would expect from noise alone.
    """
    m = len(p_values)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p_values[i])
    adjusted = [1.0] * m
    running = 0.0
    for rank, index in enumerate(order):
        value = (m - rank) * p_values[index]
        running = max(running, value)
        adjusted[index] = min(1.0, running)
    return adjusted


def apply_holm(
    comparisons: Sequence[PairedComparison],
) -> list[PairedComparison]:
    """Return copies of `comparisons` with Holm-adjusted p-values filled in."""
    from dataclasses import replace

    adjusted = holm_bonferroni([c.sign_test_p for c in comparisons])
    return [replace(c, holm_adjusted_p=adjusted[i]) for i, c in enumerate(comparisons)]
