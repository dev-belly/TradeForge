"""Execute an experiment grid and aggregate it honestly.

Two resampling units exist in this project and they are not interchangeable:

  * **Within a session**, consecutive executions are autocorrelated (volatility
    clusters, liquidity droughts). The moving block bootstrap is required.
  * **Across sessions**, each synthetic session is an independent draw from the
    generator. There is no block structure to preserve, so the resampling unit
    is one session and the block length is 1. Using blocks here would not add
    information; it would only reduce the effective sample size.

`run_experiment` uses the second. `bootstrap.block_bootstrap_ci` is used with
the configured block length for the first, in the per-execution analyses.

Pairing is by seed: cell A and cell B in the same repetition see the *same*
market, so the difference between them is not contaminated by session variance.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean
from typing import Any

from ..application.harness import ExecutionHarness
from ..infrastructure.logging import get_logger, log_event
from .bootstrap import ConfidenceInterval, block_bootstrap_ci
from .compare import PairedComparison, apply_holm, paired_comparison
from .experiments import EXPERIMENT_CAVEATS, ExperimentSpec

SESSION_BLOCK_LENGTH = 1


@dataclass(frozen=True, slots=True)
class CellAggregate:
    """One grid cell, aggregated over the sessions it was run on."""

    label: str
    n_runs: int
    n_usable: int
    metric_values: tuple[float, ...]
    ci: ConfidenceInterval
    mean_fill_ratio: float
    mean_participation_rate: float
    mean_fees_bps: float
    mean_maker_ratio: float
    tags: dict[str, str]

    @property
    def metric_mean(self) -> float:
        return self.ci.estimate

    def to_dict(self) -> dict[str, object]:
        return {
            "label": self.label,
            "n_runs": self.n_runs,
            "n_usable": self.n_usable,
            "metric_mean": self.metric_mean,
            "ci": self.ci.to_dict(),
            "mean_fill_ratio": self.mean_fill_ratio,
            "mean_participation_rate": self.mean_participation_rate,
            "mean_fees_bps": self.mean_fees_bps,
            "mean_maker_ratio": self.mean_maker_ratio,
            "tags": dict(self.tags),
        }


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    name: str
    description: str
    metric: str
    seeds: tuple[int, ...]
    cells: tuple[CellAggregate, ...]
    comparisons: tuple[PairedComparison, ...]
    rows: tuple[dict[str, Any], ...]
    config_fingerprint: str
    caveats: tuple[str, ...] = EXPERIMENT_CAVEATS

    def cell(self, label: str) -> CellAggregate:
        for candidate in self.cells:
            if candidate.label == label:
                return candidate
        raise KeyError(f"no cell {label!r} in experiment {self.name}")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "metric": self.metric,
            "seeds": list(self.seeds),
            "config_fingerprint": self.config_fingerprint,
            "cells": [c.to_dict() for c in self.cells],
            "comparisons": [c.to_dict() for c in self.comparisons],
            "n_rows": len(self.rows),
            "caveats": list(self.caveats),
        }


def run_experiment(
    harness: ExecutionHarness,
    spec: ExperimentSpec,
    *,
    seeds: Sequence[int],
    bootstrap_seed: int = 11,
    n_resamples: int = 2000,
    confidence: float = 0.95,
) -> ExperimentResult:
    """Run every cell on every seed, then aggregate and compare.

    No cell is skipped. If a run raises, the exception propagates: a silently
    dropped cell is a silently biased experiment.
    """
    if not seeds:
        raise ValueError("an experiment needs at least one seed")
    logger = get_logger("research")
    values: dict[str, list[float]] = {cell.label: [] for cell in spec.cells}
    ratios: dict[str, list[float]] = {cell.label: [] for cell in spec.cells}
    participations: dict[str, list[float]] = {cell.label: [] for cell in spec.cells}
    fees: dict[str, list[float]] = {cell.label: [] for cell in spec.cells}
    makers: dict[str, list[float]] = {cell.label: [] for cell in spec.cells}
    rows: list[dict[str, Any]] = []

    for seed in seeds:
        for cell in spec.cells:
            bound = cell.with_seed(seed)
            context = harness.run(bound.request)
            report = context.tca
            if report is None or context.result is None:
                raise RuntimeError(f"run {bound.label} produced no TCA report")
            value = _metric_value(report, spec.metric)
            if value is not None:
                values[cell.label].append(value)
            ratios[cell.label].append(report.metrics.fill_ratio)
            participations[cell.label].append(report.metrics.participation_rate)
            fees[cell.label].append(report.attribution.fees_bps or 0.0)
            makers[cell.label].append(report.metrics.maker_fill_ratio)
            rows.append(
                {
                    "experiment": spec.name,
                    "cell": cell.label,
                    "seed": seed,
                    **cell.tags,
                    **report.metrics.to_dict(),
                    "attribution": report.attribution.to_dict(),
                    "queue_mode": report.provenance.get("queue_mode"),
                    "latency_basis": report.provenance.get("latency_basis"),
                    "counterfactual_mode": report.provenance.get("counterfactual_mode"),
                    "config_fingerprint": harness.fingerprint,
                }
            )
        log_event(logger, 20, "seed complete", experiment=spec.name, seed=seed)

    cells = tuple(
        _aggregate(
            cell,
            values[cell.label],
            ratios[cell.label],
            participations[cell.label],
            fees[cell.label],
            makers[cell.label],
            bootstrap_seed=bootstrap_seed,
            n_resamples=n_resamples,
            confidence=confidence,
        )
        for cell in spec.cells
    )
    comparisons = _compare(
        spec,
        values,
        seeds=seeds,
        bootstrap_seed=bootstrap_seed,
        n_resamples=n_resamples,
        confidence=confidence,
    )
    return ExperimentResult(
        name=spec.name,
        description=spec.description,
        metric=spec.metric,
        seeds=tuple(int(s) for s in seeds),
        cells=cells,
        comparisons=comparisons,
        rows=tuple(rows),
        config_fingerprint=harness.fingerprint,
    )


# ----------------------------------------------------------------- internals


def _metric_value(report: Any, metric: str) -> float | None:
    value = getattr(report.metrics, metric, None)
    return None if value is None else float(value)


def _aggregate(
    cell: Any,
    metric_values: list[float],
    ratios: list[float],
    participations: list[float],
    fees: list[float],
    makers: list[float],
    *,
    bootstrap_seed: int,
    n_resamples: int,
    confidence: float,
) -> CellAggregate:
    if metric_values:
        ci = block_bootstrap_ci(
            metric_values,
            block_length=SESSION_BLOCK_LENGTH,
            n_resamples=n_resamples,
            confidence=confidence,
            seed=bootstrap_seed,
        )
    else:
        # Every run in this cell was unmeasurable. Say so with an explicit
        # sentinel rather than inventing a zero.
        ci = ConfidenceInterval(
            estimate=float("nan"),
            lower=float("nan"),
            upper=float("nan"),
            confidence=confidence,
            n_observations=0,
            n_resamples=0,
            method="not_measurable",
            block_length=SESSION_BLOCK_LENGTH,
            seed=bootstrap_seed,
        )
    return CellAggregate(
        label=cell.label,
        n_runs=len(ratios),
        n_usable=len(metric_values),
        metric_values=tuple(metric_values),
        ci=ci,
        mean_fill_ratio=fmean(ratios) if ratios else 0.0,
        mean_participation_rate=fmean(participations) if participations else 0.0,
        mean_fees_bps=fmean(fees) if fees else 0.0,
        mean_maker_ratio=fmean(makers) if makers else 0.0,
        tags=dict(cell.tags),
    )


def _compare(
    spec: ExperimentSpec,
    values: dict[str, list[float]],
    *,
    seeds: Sequence[int],
    bootstrap_seed: int,
    n_resamples: int,
    confidence: float,
) -> tuple[PairedComparison, ...]:
    """Pair every cell against the declared baseline, on identical seeds."""
    if spec.baseline is None or spec.baseline not in values:
        return []
    baseline_values = values[spec.baseline]
    comparisons: list[PairedComparison] = []
    for cell in spec.cells:
        if cell.label == spec.baseline:
            continue
        candidate = values[cell.label]
        # Pairing requires the same sessions to have produced usable values in
        # both cells. Unusable pairs are dropped together, never one-sided.
        if len(candidate) != len(baseline_values) or not candidate:
            continue
        comparisons.append(
            paired_comparison(
                cell.label,
                spec.baseline,
                candidate,
                baseline_values,
                metric=spec.metric,
                block_length=SESSION_BLOCK_LENGTH,
                n_resamples=n_resamples,
                confidence=confidence,
                seed=bootstrap_seed,
            )
        )
    del seeds
    return tuple(apply_holm(comparisons))
