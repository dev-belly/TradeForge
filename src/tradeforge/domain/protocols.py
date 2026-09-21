"""Protocols: the only abstraction points in the system.

Six change points, nothing else (see design-review §19). Using a Protocol
instead of an ABC keeps the domain free of inheritance hierarchies and lets
infrastructure implementations stay framework-agnostic.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from decimal import Decimal
from typing import Protocol, runtime_checkable

from .book import BookSnapshot, MarketState
from .enums import DataType, LiquidityFlag, QueueMode, Side
from .events import MarketEvent
from .fills import ExecutionReport
from .instrument import InstrumentSpec
from .orders import ChildOrder


@runtime_checkable
class BookProtocol(Protocol):
    """Reconstructed limit order book."""

    def apply(self, event: MarketEvent) -> None:
        """Apply one normalized event."""

    def snapshot(self, depth: int) -> BookSnapshot:
        """Immutable view of the top `depth` levels per side."""

    def level_size_base(self, side: Side, price_ticks: int) -> int:
        """Displayed size at one price (read before an event mutates it)."""

    @property
    def last_sequence_id(self) -> int: ...


@runtime_checkable
class MarketDataAdapter(Protocol):
    """Source-specific decoding lives here and nowhere else."""

    @property
    def name(self) -> str: ...

    @property
    def data_type(self) -> DataType:
        """Declared capability tier. Never optimistic."""

    def events(self) -> Iterator[MarketEvent]:
        """Lazily yield normalized events in source order."""


@runtime_checkable
class ExecutionPolicy(Protocol):
    """Decides child orders. Receives value objects only."""

    @property
    def name(self) -> str: ...

    def on_market_event(self, state: MarketState) -> Sequence[ChildOrder]:
        """React to the current observable state."""

    def on_timer(self, state: MarketState) -> Sequence[ChildOrder]:
        """Scheduled slice boundary."""

    def on_fill(self, report: ExecutionReport) -> None: ...

    def on_reject(self, report: ExecutionReport) -> None: ...


@runtime_checkable
class QueueModel(Protocol):
    """Tracks (exactly or approximately) how much is ahead of our order."""

    @property
    def mode(self) -> QueueMode: ...

    def register(
        self,
        client_order_id: str,
        *,
        side: Side,
        price_ticks: int,
        quantity_base: int,
        level_size_base: int,
    ) -> None:
        """Place one of our orders at the back of a price level."""

    def on_trade(
        self,
        *,
        price_ticks: int,
        quantity_base: int,
        order_id: int | None = None,
        side: Side | None = None,
    ) -> list[tuple[str, int]]:
        """Consume the front of the queue; return (order id, filled qty) pairs."""

    def on_size_decrease(
        self,
        *,
        price_ticks: int,
        removed_base: int,
        level_size_before: int,
        order_id: int | None = None,
        side: Side | None = None,
    ) -> None:
        """Level shrank without a trade: unattributable from L2, exact from L3."""

    def release(self, client_order_id: str) -> None:
        """Our order left the book (cancelled / expired / filled)."""

    def queue_ahead_base(self, client_order_id: str) -> int:
        """Estimated (or exact) shares ahead of our order."""

    def fillable_base(self, client_order_id: str) -> int:
        """Shares that can be filled right now: 0 unless the queue is exhausted."""


@runtime_checkable
class Clock(Protocol):
    """Simulation time and scheduled actions.

    Declared here rather than imported from the replay package so that the
    execution simulator depends on an abstraction, not on the replay driver.
    That keeps the dependency direction one-way: replay drives execution, never
    the reverse.
    """

    @property
    def now_ns(self) -> int: ...

    def schedule(self, due_ns: int, action: Callable[[int], None], label: str = "") -> int: ...

    @property
    def pending(self) -> int: ...


@runtime_checkable
class LatencyModel(Protocol):
    """Decision -> arrival delay. Applied by advancing the clock, never sleep."""

    @property
    def basis(self) -> str:
        """`scenario` or `observed`."""

    def arrival_ns(self, decision_ns: int) -> int: ...


@runtime_checkable
class CostModel(Protocol):
    """Fees and spread cost. All parameters come from config."""

    def fee(self, *, notional: Decimal, liquidity: LiquidityFlag) -> Decimal:
        """Total fee (may be negative for a maker rebate)."""

    def spread_cost_bps(
        self, *, side: Side, decision_mid_ticks: float, fill_price_ticks: int
    ) -> float: ...


@runtime_checkable
class ImpactModel(Protocol):
    """Simplified research impact approximation (never claimed as calibrated)."""

    @property
    def name(self) -> str: ...

    def impact_bps(self, *, participation_rate: float, sigma_bps: float) -> float: ...


@runtime_checkable
class InstrumentProvider(Protocol):
    def spec(self, symbol: str) -> InstrumentSpec: ...


__all__ = [
    "BookProtocol",
    "Clock",
    "CostModel",
    "ExecutionPolicy",
    "ImpactModel",
    "InstrumentProvider",
    "LatencyModel",
    "MarketDataAdapter",
    "QueueModel",
]
