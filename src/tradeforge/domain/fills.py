"""Fills, fees and execution reports.

Sign convention (system-wide): positive cost = worse execution.
Markout for a BUY = future_mid - fill_price; for a SELL the sign is flipped
(see `markout_bps`).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .enums import LiquidityFlag, OrderStatus, ReportType, Side


@dataclass(frozen=True, slots=True)
class Fill:
    """One executed quantity at one price."""

    fill_id: int
    order_id: int
    client_order_id: str
    parent_order_id: str
    symbol: str
    side: Side
    price_ticks: int
    quantity_base: int
    timestamp_ns: int
    liquidity_flag: LiquidityFlag
    fee: Decimal
    queue_wait_ns: int = 0
    sequence_id: int = 0

    def notional(self, tick_size: Decimal) -> Decimal:
        """Exact notional for this fill. A method, not a property.

        It was declared `@property` while taking `tick_size`, so any call would
        have raised `TypeError: 'Decimal' object is not callable` - a property
        cannot accept arguments. Nothing called it, which is exactly why it
        survived: dead code that fails is worse than dead code that does not,
        because it looks usable.
        """
        return Decimal(self.price_ticks) * tick_size * Decimal(self.quantity_base)


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """Everything a policy is told about one of its orders."""

    report_type: ReportType
    client_order_id: str
    order_id: int
    parent_order_id: str
    timestamp_ns: int
    status: OrderStatus
    filled_base: int = 0
    remaining_base: int = 0
    fill: Fill | None = None
    reason: str = ""

    @property
    def is_fill(self) -> bool:
        return self.fill is not None


def markout_bps(side: Side, fill_price_ticks: float, future_mid_ticks: float) -> float:
    """Post-fill markout in bps, positive = adverse (price moved against us).

    BUY:  future_mid - fill_price   (price went up after we bought  => good)
    SELL: fill_price - future_mid
    We return the *adverse* value, so a positive number always means the fill
    was adversely selected.
    """
    if fill_price_ticks <= 0:
        raise ValueError("fill price must be positive")
    raw = (future_mid_ticks - fill_price_ticks) / fill_price_ticks * 10_000.0
    return -raw if side is Side.BUY else raw


def signed_cost_bps(
    side: Side, execution_price_ticks: float, benchmark_price_ticks: float
) -> float:
    """Implementation-shortfall style cost in bps.

    Positive = worse execution (we paid more than the benchmark for a buy).
    """
    if benchmark_price_ticks <= 0:
        raise ValueError("benchmark price must be positive")
    raw = (execution_price_ticks - benchmark_price_ticks) / benchmark_price_ticks * 10_000.0
    return raw * side.sign
