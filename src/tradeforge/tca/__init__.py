"""Transaction cost analysis.

Read order: `observer` -> `benchmarks` -> `metrics` -> `attribution` ->
`markouts` -> `report`.

Sign convention throughout: **positive basis points = worse execution.**
"""

from .attribution import CostAttribution, compute_attribution
from .benchmarks import BenchmarkPrices, compute_benchmarks
from .markouts import DEFAULT_HORIZONS_NS, MarkoutSummary, compute_markouts
from .metrics import CostMetrics, compute_cost_metrics
from .observer import MarketObserver, MidPoint, TradePrint
from .report import TcaReport, build_tca_report, max_markout_horizon_ns

__all__ = [
    "DEFAULT_HORIZONS_NS",
    "BenchmarkPrices",
    "CostAttribution",
    "CostMetrics",
    "MarketObserver",
    "MarkoutSummary",
    "MidPoint",
    "TcaReport",
    "TradePrint",
    "build_tca_report",
    "compute_attribution",
    "compute_benchmarks",
    "compute_cost_metrics",
    "compute_markouts",
    "max_markout_horizon_ns",
]
