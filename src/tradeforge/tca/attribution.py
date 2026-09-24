"""Cost attribution: where the basis points actually went.

The decomposition is additive **by construction**:

    is_filled_bps = spread_cost_bps + fees_bps + timing_bps + residual_impact_bps

and it is a *residual* decomposition, not a claim of measurement. The residual
absorbs two things:

  1. the difference between using `mid_at_fill` and `arrival` as denominators,
     which is a second-order approximation error;
  2. genuine market impact, which this platform does **not** claim to identify
     causally from a replay in which our orders never altered the tape.

So the residual is reported as `residual_impact_bps` and labelled "unexplained"
in every report. It is never presented as a measured impact number. Measuring
impact requires either a controlled experiment or an instrumented venue, and
TradeForge has neither.

Component signs (positive = worse):

  * `spread_cost_bps`  - crossing the spread. Negative for passive fills, which
                         earn it instead of paying it.
  * `fees_bps`         - exchange fees + commission, net of maker rebates.
  * `timing_bps`       - how far the mid drifted between arrival and each fill.
                         This is market drift, not a decision error.
  * `opportunity_bps`  - Perold's charge on the quantity we failed to execute.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..domain.fills import Fill, signed_cost_bps
from ..domain.instrument import InstrumentSpec
from ..execution.result import ExecutionResult
from .benchmarks import BenchmarkPrices
from .observer import MarketObserver

BPS = 10_000.0


@dataclass(frozen=True, slots=True)
class CostAttribution:
    """Additive cost decomposition. All `*_bps` fields: positive = worse."""

    fill_ratio: float
    #: Per filled share.
    is_filled_bps: float | None
    #: Per requested share (this is the headline number).
    is_total_bps: float | None

    spread_cost_bps: float | None
    fees_bps: float | None
    timing_bps: float | None
    residual_impact_bps: float | None
    opportunity_cost_bps: float | None

    #: spread + fees + timing, so a reader can verify additivity by hand.
    explained_bps: float | None
    #: Fills with no mid reference, excluded from spread/timing.
    n_fills_without_mid: int
    n_fills: int

    @property
    def residual_share(self) -> float | None:
        """Fraction of `is_filled_bps` the residual explains.

        A large share means the decomposition is not telling the story, and the
        report says so rather than presenting four tidy numbers.
        """
        if not self.is_filled_bps or self.residual_impact_bps is None:
            return None
        return self.residual_impact_bps / self.is_filled_bps

    def to_dict(self) -> dict[str, object]:
        return {
            "fill_ratio": self.fill_ratio,
            "is_filled_bps": self.is_filled_bps,
            "is_total_bps": self.is_total_bps,
            "spread_cost_bps": self.spread_cost_bps,
            "fees_bps": self.fees_bps,
            "timing_bps": self.timing_bps,
            "residual_impact_bps": self.residual_impact_bps,
            "opportunity_cost_bps": self.opportunity_cost_bps,
            "explained_bps": self.explained_bps,
            "residual_share": self.residual_share,
            "n_fills": self.n_fills,
            "n_fills_without_mid": self.n_fills_without_mid,
            "residual_impact_label": "unexplained residual, not a measured impact",
        }


def compute_attribution(
    result: ExecutionResult,
    benchmarks: BenchmarkPrices,
    observer: MarketObserver,
    spec: InstrumentSpec,
) -> CostAttribution:
    """Decompose realised cost into spread, fees, timing, residual, opportunity."""
    del spec  # tick size is not needed: every ratio is already dimensionless
    arrival = benchmarks.arrival_mid_ticks
    side = result.side

    spread = _volume_weighted_spread(result.fills, observer)
    timing = _volume_weighted_timing(result.fills, observer, arrival)
    fees = _fees_bps(result)
    without_mid = _count_fills_without_mid(result.fills, observer)
    explained = (
        None
        if spread is None and timing is None
        else ((spread or 0.0) + (fees or 0.0) + (timing or 0.0))
    )

    is_filled: float | None = None
    if arrival is not None and arrival > 0 and result.avg_fill_price_ticks is not None:
        is_filled = signed_cost_bps(side, result.avg_fill_price_ticks, arrival)

    residual: float | None = None
    if is_filled is not None and explained is not None:
        residual = is_filled - explained

    opportunity: float | None = None
    terminal = benchmarks.terminal_mid_ticks
    if arrival is not None and arrival > 0 and terminal is not None:
        opportunity = signed_cost_bps(side, terminal, arrival)

    is_total: float | None = None
    r = result.completion_rate
    if (r == 0 or is_filled is not None) and (r == 1 or opportunity is not None):
        is_total = (is_filled or 0.0) * r + (opportunity or 0.0) * (1.0 - r)

    return CostAttribution(
        fill_ratio=result.completion_rate,
        is_filled_bps=is_filled,
        is_total_bps=is_total,
        spread_cost_bps=spread,
        fees_bps=fees,
        timing_bps=timing,
        residual_impact_bps=residual,
        opportunity_cost_bps=opportunity,
        explained_bps=explained,
        n_fills_without_mid=without_mid,
        n_fills=len(result.fills),
    )


# --------------------------------------------------------------------- pieces


def _fees_bps(result: ExecutionResult) -> float | None:
    """Fees as a fraction of executed notional. Negative = net rebate."""
    if result.notional <= 0:
        return None
    return float(result.fees_total / Decimal(result.notional) * Decimal(str(BPS)))


def _count_fills_without_mid(fills: tuple[Fill, ...], observer: MarketObserver) -> int:
    return sum(1 for fill in fills if observer.mid_strictly_before(fill.timestamp_ns) is None)


def _volume_weighted_spread(fills: tuple[Fill, ...], observer: MarketObserver) -> float | None:
    """Signed (fill price - mid before fill) / mid, volume weighted.

    Positive for taker fills (we crossed), negative for maker fills (we earned
    the spread). That is the whole point of measuring it per fill rather than
    assuming a half-spread cost.
    """
    total_qty = 0
    weighted = 0.0
    for fill in fills:
        mid = observer.mid_strictly_before(fill.timestamp_ns)
        if mid is None or mid <= 0:
            continue
        weighted += signed_cost_bps(fill.side, float(fill.price_ticks), mid) * fill.quantity_base
        total_qty += fill.quantity_base
    return None if total_qty == 0 else weighted / total_qty


def _volume_weighted_timing(
    fills: tuple[Fill, ...], observer: MarketObserver, arrival: float | None
) -> float | None:
    """Signed (mid before fill - arrival) / arrival, volume weighted.

    This is drift between the decision instant and the fill, not a decision
    error: a strategy cannot control where the market goes. It is separated out
    so the spread and residual columns are not contaminated by it.
    """
    if arrival is None or arrival <= 0:
        return None
    total_qty = 0
    weighted = 0.0
    for fill in fills:
        mid = observer.mid_strictly_before(fill.timestamp_ns)
        if mid is None:
            continue
        weighted += signed_cost_bps(fill.side, mid, arrival) * fill.quantity_base
        total_qty += fill.quantity_base
    return None if total_qty == 0 else weighted / total_qty
