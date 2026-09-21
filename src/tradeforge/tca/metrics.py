"""Benchmark-relative execution cost.

Sign convention is system-wide: **positive bps = worse execution.**

Every benchmark comparison is signed through `Side.sign`, so a BUY that fills
below the benchmark and a SELL that fills above it both report a negative
(number-good) cost. That means a table of these numbers can be averaged,
compared and bootstrapped without ever flipping a sign by hand.

`implementation_shortfall_bps` is Perold's definition: the filled part is
measured against the arrival price, and the unfilled part is charged at the
end-of-window price. Both legs are weighted by their share of the *requested*
quantity, so a strategy that fills 10% cheaply does not look good.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import Side
from ..domain.fills import signed_cost_bps
from ..domain.instrument import InstrumentSpec
from ..execution.result import ExecutionResult
from .benchmarks import BenchmarkPrices

BPS = 10_000.0


@dataclass(frozen=True, slots=True)
class CostMetrics:
    """One row of the TCA table. All `*_bps` fields are positive = worse."""

    parent_order_id: str
    policy: str
    side: Side

    requested_base: int
    filled_base: int
    unfilled_base: int
    fill_ratio: float

    avg_fill_price_ticks: float | None
    arrival_mid_ticks: float | None
    interval_vwap_ticks: float | None
    interval_twap_ticks: float | None
    interval_mid_ticks: float | None
    terminal_mid_ticks: float | None

    cost_vs_arrival_bps: float | None
    cost_vs_vwap_bps: float | None
    cost_vs_twap_bps: float | None
    cost_vs_interval_mid_bps: float | None
    cost_vs_terminal_bps: float | None
    implementation_shortfall_bps: float | None

    participation_rate: float
    maker_fill_ratio: float
    window_volume_base: int

    # Provenance of the benchmark, so a weak benchmark cannot masquerade as a
    # strong one in a table that only prints the cost column.
    n_mid_observations: int = 0
    n_trade_prints: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "parent_order_id": self.parent_order_id,
            "policy": self.policy,
            "side": self.side.value,
            "requested_base": self.requested_base,
            "filled_base": self.filled_base,
            "unfilled_base": self.unfilled_base,
            "fill_ratio": self.fill_ratio,
            "avg_fill_price_ticks": self.avg_fill_price_ticks,
            "arrival_mid_ticks": self.arrival_mid_ticks,
            "interval_vwap_ticks": self.interval_vwap_ticks,
            "interval_twap_ticks": self.interval_twap_ticks,
            "interval_mid_ticks": self.interval_mid_ticks,
            "terminal_mid_ticks": self.terminal_mid_ticks,
            "cost_vs_arrival_bps": self.cost_vs_arrival_bps,
            "cost_vs_vwap_bps": self.cost_vs_vwap_bps,
            "cost_vs_twap_bps": self.cost_vs_twap_bps,
            "cost_vs_interval_mid_bps": self.cost_vs_interval_mid_bps,
            "cost_vs_terminal_bps": self.cost_vs_terminal_bps,
            "implementation_shortfall_bps": self.implementation_shortfall_bps,
            "participation_rate": self.participation_rate,
            "maker_fill_ratio": self.maker_fill_ratio,
            "window_volume_base": self.window_volume_base,
            "n_mid_observations": self.n_mid_observations,
            "n_trade_prints": self.n_trade_prints,
        }


def compute_cost_metrics(
    result: ExecutionResult,
    benchmarks: BenchmarkPrices,
    spec: InstrumentSpec,
) -> CostMetrics:
    """Compare one execution against every window benchmark."""
    side = result.side
    avg_fill = result.avg_fill_price_ticks
    arrival = benchmarks.arrival_mid_ticks
    fill_ratio = result.completion_rate

    return CostMetrics(
        parent_order_id=result.parent_order_id,
        policy=result.policy_name,
        side=side,
        requested_base=result.requested_base,
        filled_base=result.filled_base,
        unfilled_base=result.unfilled_base,
        fill_ratio=fill_ratio,
        avg_fill_price_ticks=avg_fill,
        arrival_mid_ticks=arrival,
        interval_vwap_ticks=benchmarks.interval_vwap_ticks,
        interval_twap_ticks=benchmarks.interval_twap_ticks,
        interval_mid_ticks=benchmarks.interval_mid_ticks,
        terminal_mid_ticks=benchmarks.terminal_mid_ticks,
        cost_vs_arrival_bps=_cost(side, avg_fill, arrival),
        cost_vs_vwap_bps=_cost(side, avg_fill, benchmarks.interval_vwap_ticks),
        cost_vs_twap_bps=_cost(side, avg_fill, benchmarks.interval_twap_ticks),
        cost_vs_interval_mid_bps=_cost(side, avg_fill, benchmarks.interval_mid_ticks),
        cost_vs_terminal_bps=_cost(side, avg_fill, benchmarks.terminal_mid_ticks),
        implementation_shortfall_bps=_implementation_shortfall(
            side=side,
            avg_fill=avg_fill,
            arrival=arrival,
            terminal=benchmarks.terminal_mid_ticks,
            fill_ratio=fill_ratio,
        ),
        participation_rate=result.participation_rate,
        maker_fill_ratio=result.maker_fill_ratio,
        window_volume_base=benchmarks.window_volume_base,
        n_mid_observations=benchmarks.n_mid_observations,
        n_trade_prints=benchmarks.n_trade_prints,
    )


def _cost(side: Side, execution: float | None, benchmark: float | None) -> float | None:
    if execution is None or benchmark is None or benchmark <= 0:
        return None
    return signed_cost_bps(side, execution, benchmark)


def _implementation_shortfall(
    *,
    side: Side,
    avg_fill: float | None,
    arrival: float | None,
    terminal: float | None,
    fill_ratio: float,
) -> float | None:
    """Perold IS per *requested* share, in bps, positive = worse.

    Filled leg: what we actually paid versus the decision price.
    Unfilled leg: what we would have paid versus the decision price, charged as
    opportunity cost. A partial fill is not free.
    """
    if arrival is None or arrival <= 0:
        return None
    filled_part = 0.0
    if avg_fill is not None and fill_ratio > 0:
        filled_part = signed_cost_bps(side, avg_fill, arrival) * fill_ratio
    unfilled_part = 0.0
    if terminal is not None and fill_ratio < 1.0:
        unfilled_part = signed_cost_bps(side, terminal, arrival) * (1.0 - fill_ratio)
    return filled_part + unfilled_part
