"""The TCA report: one execution, fully explained, with its caveats attached.

Every report carries the provenance that makes its numbers interpretable:

  * which dataset and capability tier produced it;
  * whether the queue position was EXACT or APPROXIMATE;
  * whether latency was a scenario or observed;
  * that the counterfactual mode is `replay_approximation`;
  * how many observations each benchmark rests on.

A cost number without those is not a result, it is a rumour. The report refuses
to hide them behind a formatting layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..domain.instrument import InstrumentSpec
from ..execution.result import ExecutionResult
from .attribution import CostAttribution, compute_attribution
from .benchmarks import BenchmarkPrices, compute_benchmarks
from .markouts import DEFAULT_HORIZONS_NS, MarkoutSummary, compute_markouts
from .metrics import CostMetrics, compute_cost_metrics
from .observer import MarketObserver

MARKOUT_CAVEAT = (
    "Markouts use post-fill data by construction. They are a measurement of "
    "what happened, never an input to any policy."
)
APPROXIMATION_CAVEAT = (
    "Replay is counterfactual: our simulated orders did not alter the historical "
    "event stream. Our own market impact on future events is therefore not "
    "modelled, and the impact column is an unexplained residual."
)
QUEUE_CAVEAT = (
    "Queue position is an ESTIMATE derived from aggregated level data. Exact "
    "queue position requires L3/MBO order identity."
)


@dataclass(frozen=True, slots=True)
class TcaReport:
    """A complete, self-describing execution cost analysis."""

    metrics: CostMetrics
    attribution: CostAttribution
    benchmarks: BenchmarkPrices
    markouts: tuple[MarkoutSummary, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    caveats: tuple[str, ...] = ()

    # --------------------------------------------------------------- exports

    @property
    def headline(self) -> dict[str, object]:
        """The four numbers a desk actually reads, with their basis stated."""
        return {
            "policy": self.metrics.policy,
            "side": self.metrics.side.value,
            "fill_ratio": self.metrics.fill_ratio,
            "participation_rate": self.metrics.participation_rate,
            "cost_vs_arrival_bps": self.metrics.cost_vs_arrival_bps,
            "cost_vs_vwap_bps": self.metrics.cost_vs_vwap_bps,
            "cost_vs_twap_bps": self.metrics.cost_vs_twap_bps,
            "implementation_shortfall_bps": self.metrics.implementation_shortfall_bps,
            "maker_fill_ratio": self.metrics.maker_fill_ratio,
            "queue_mode": self.provenance.get("queue_mode"),
            "latency_basis": self.provenance.get("latency_basis"),
            "counterfactual_mode": self.provenance.get("counterfactual_mode"),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "headline": self.headline,
            "metrics": self.metrics.to_dict(),
            "attribution": self.attribution.to_dict(),
            "benchmarks": self.benchmarks.to_dict(),
            "markouts": [m.to_dict() for m in self.markouts],
            "provenance": dict(self.provenance),
            "caveats": list(self.caveats),
        }


def build_tca_report(
    result: ExecutionResult,
    observer: MarketObserver,
    spec: InstrumentSpec,
    *,
    provenance: dict[str, Any] | None = None,
    markout_horizons_ns: tuple[int, ...] = DEFAULT_HORIZONS_NS,
) -> TcaReport:
    """Assemble benchmarks, costs, attribution and markouts for one execution."""
    benchmarks = compute_benchmarks(observer, start_ns=result.start_ns, end_ns=result.end_ns)
    metrics = compute_cost_metrics(result, benchmarks, spec)
    attribution = compute_attribution(result, benchmarks, observer, spec)
    markouts = compute_markouts(result.fills, observer, markout_horizons_ns)

    merged: dict[str, Any] = dict(provenance or {})
    merged.setdefault("queue_mode", result.queue_mode)
    merged.setdefault("latency_basis", result.latency_basis)
    merged.setdefault("counterfactual_mode", result.counterfactual_mode)
    merged["markout_horizons_ns"] = list(markout_horizons_ns)

    caveats = [APPROXIMATION_CAVEAT, MARKOUT_CAVEAT]
    if result.queue_mode.upper() != "EXACT":
        caveats.append(QUEUE_CAVEAT)

    return TcaReport(
        metrics=metrics,
        attribution=attribution,
        benchmarks=benchmarks,
        markouts=markouts,
        provenance=merged,
        caveats=tuple(caveats),
    )


def max_markout_horizon_ns(
    horizons_ns: tuple[int, ...] = DEFAULT_HORIZONS_NS,
) -> int:
    """How far past the window the replay must run for markouts to be measurable."""
    return max(horizons_ns) if horizons_ns else 0
