"""Resampling statistics for execution cost.

Two things this module refuses to do, both because they would manufacture
confidence that the data does not support:

  1. **i.i.d. bootstrap on a time series.** Execution costs are
     autocorrelated: volatility clusters, spreads widen together, and a
     liquidity drought persists across many executions. Resampling individual
     observations destroys that structure and produces intervals that are far
     too narrow. We use the **moving block bootstrap** instead, which resamples
     contiguous blocks and preserves short-range dependence.

  2. **Reporting a point estimate without its interval.** Every public function
     here returns a `ConfidenceInterval` carrying the method, the number of
     resamples, the block length and the observation count, so a reader can
     judge how much the interval is worth.

All resampling is seeded, so a result is reproducible from its config.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from statistics import fmean

Statistic = Callable[[Sequence[float]], float]


@dataclass(frozen=True, slots=True)
class ConfidenceInterval:
    """A point estimate with a resampled interval and its provenance."""

    estimate: float
    lower: float
    upper: float
    confidence: float
    n_observations: int
    n_resamples: int
    method: str
    block_length: int = 0
    seed: int = 0

    @property
    def width(self) -> float:
        return self.upper - self.lower

    @property
    def excludes_zero(self) -> bool:
        """Whether the interval lies entirely on one side of zero.

        Reported instead of a bare p-value: "the interval excludes zero" is a
        statement about the estimate, not a ritual threshold.
        """
        return self.lower > 0.0 or self.upper < 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "estimate": self.estimate,
            "lower": self.lower,
            "upper": self.upper,
            "width": self.width,
            "confidence": self.confidence,
            "n_observations": self.n_observations,
            "n_resamples": self.n_resamples,
            "method": self.method,
            "block_length": self.block_length,
            "seed": self.seed,
            "excludes_zero": self.excludes_zero,
        }


def _percentile(ordered: Sequence[float], fraction: float) -> float:
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _resample_indices(n: int, block_length: int, rng: random.Random) -> list[int]:
    """Moving block bootstrap: sample blocks with replacement, concatenate."""
    block = max(1, min(block_length, n))
    n_blocks = math.ceil(n / block)
    starts = [rng.randrange(0, n - block + 1) for _ in range(n_blocks)]
    indices: list[int] = []
    for start in starts:
        indices.extend(range(start, start + block))
    return indices[:n]


def block_bootstrap_ci(
    values: Sequence[float],
    *,
    block_length: int = 50,
    n_resamples: int = 2000,
    confidence: float = 0.95,
    seed: int = 11,
    statistic: Statistic = fmean,
) -> ConfidenceInterval:
    """Moving block bootstrap interval for `statistic(values)`.

    `block_length` should be at least as long as the dependence horizon you
    believe exists. The default (50) is a config value, not a measurement; the
    report prints it next to every interval so the assumption is visible.
    """
    sample = [float(v) for v in values]
    if not sample:
        raise ValueError("cannot bootstrap an empty sample")
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be in (0, 1), got {confidence}")
    if n_resamples <= 0:
        raise ValueError(f"n_resamples must be positive, got {n_resamples}")

    estimate = statistic(sample)
    if len(sample) == 1:
        return ConfidenceInterval(
            estimate=estimate,
            lower=estimate,
            upper=estimate,
            confidence=confidence,
            n_observations=1,
            n_resamples=0,
            method="block_bootstrap_degenerate",
            block_length=1,
            seed=seed,
        )

    rng = random.Random(seed)
    draws: list[float] = []
    for _ in range(n_resamples):
        indices = _resample_indices(len(sample), block_length, rng)
        draws.append(statistic([sample[i] for i in indices]))
    draws.sort()

    alpha = (1.0 - confidence) / 2.0
    lower = _percentile(draws, alpha)
    upper = _percentile(draws, 1.0 - alpha)
    # Floating-point guard, not a statistical adjustment. When every resample
    # reproduces the sample (a short series with a long block), the percentile
    # of the draws can land two ulps above the directly computed estimate,
    # producing an interval that excludes its own point estimate. Any consumer
    # will read that as a bug, and they would be right.
    lower = min(lower, estimate)
    upper = max(upper, estimate)
    return ConfidenceInterval(
        estimate=estimate,
        lower=lower,
        upper=upper,
        confidence=confidence,
        n_observations=len(sample),
        n_resamples=n_resamples,
        method="moving_block_bootstrap",
        block_length=max(1, min(block_length, len(sample))),
        seed=seed,
    )


def iid_bootstrap_ci(
    values: Sequence[float],
    *,
    n_resamples: int = 2000,
    confidence: float = 0.95,
    seed: int = 11,
    statistic: Statistic = fmean,
) -> ConfidenceInterval:
    """Naive bootstrap, provided ONLY so a test can demonstrate it is too narrow.

    Nothing in the reporting path calls this. It exists so the claim "i.i.d.
    resampling understates the interval on autocorrelated data" is checked
    rather than asserted.
    """
    sample = [float(v) for v in values]
    if not sample:
        raise ValueError("cannot bootstrap an empty sample")
    rng = random.Random(seed)
    draws = sorted(
        statistic([sample[rng.randrange(len(sample))] for _ in sample]) for _ in range(n_resamples)
    )
    alpha = (1.0 - confidence) / 2.0
    return ConfidenceInterval(
        estimate=statistic(sample),
        lower=_percentile(draws, alpha),
        upper=_percentile(draws, 1.0 - alpha),
        confidence=confidence,
        n_observations=len(sample),
        n_resamples=n_resamples,
        method="iid_bootstrap",
        block_length=1,
        seed=seed,
    )
