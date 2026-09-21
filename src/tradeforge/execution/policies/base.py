"""Shared execution policy machinery.

A policy only ever receives `MarketState` value objects and returns child
orders. It cannot see the book object, the simulator, or the event stream, so
"strategy touches simulator internals" is structurally impossible.

All schedule-based policies share the same remaining-quantity logic:

    cumulative target after slice k  = sum(weights[:k])
    outstanding                      = filled + in-flight
    slice quantity                   = clamp(target - outstanding, 0, parent remaining)

which handles missed slices, lot rounding and late fills without special cases.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ...domain.book import MarketState
from ...domain.enums import OrderType, PlacementStyle, TimeInForce
from ...domain.fills import ExecutionReport
from ...domain.instrument import InstrumentSpec
from ...domain.orders import ChildOrder, ParentOrder
from ..oms import Oms


@dataclass(slots=True)
class PolicyState:
    """What a policy needs to remember about its own progress."""

    filled_base: int = 0
    working_base: int = 0
    slices_emitted: int = 0
    rejected_base: int = 0
    cancelled_base: int = 0


class ExecutionPolicyBase(ABC):
    """Base class for execution policies."""

    def __init__(
        self,
        *,
        parent: ParentOrder,
        spec: InstrumentSpec,
        oms: Oms,
        style: PlacementStyle = PlacementStyle.PASSIVE,
        end_of_window: str = "sweep_marketable",
        round_to_lot: bool = True,
    ) -> None:
        self._parent = parent
        self._spec = spec
        self._oms = oms
        self._style = style
        self._end_of_window = end_of_window
        self._round_to_lot = round_to_lot
        self._state = PolicyState()
        self._finished = False

    # ---------------------------------------------------------- properties

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    def parent(self) -> ParentOrder:
        return self._parent

    @property
    def style(self) -> PlacementStyle:
        return self._style

    @property
    def state(self) -> PolicyState:
        return self._state

    @property
    def parent_remaining_base(self) -> int:
        return max(
            self._parent.quantity_base - self._state.filled_base - self._state.working_base, 0
        )

    @property
    def unfilled_base(self) -> int:
        """Quantity not yet filled, ignoring anything still working.

        This is the right basis for an end-of-window sweep, because the
        simulator pulls every working child order before asking for the sweep.
        """
        return max(self._parent.quantity_base - self._state.filled_base, 0)

    @property
    def end_of_window(self) -> str:
        """`sweep_marketable` | `cancel` | `leave`."""
        return self._end_of_window

    # -------------------------------------------------------------- events

    @abstractmethod
    def on_market_event(self, state: MarketState) -> list[ChildOrder]: ...

    def on_timer(self, state: MarketState) -> list[ChildOrder]:
        return self.on_market_event(state)

    def on_window_end(self, state: MarketState) -> list[ChildOrder]:
        """Called exactly once, after the simulator pulled working orders.

        `MarketState` is the last observable state inside the parent window.
        The default is to do nothing: `cancel` and `leave` both mean the
        unfilled remainder is charged as opportunity cost.
        """
        return []

    def on_fill(self, report: ExecutionReport) -> None:
        self._state.filled_base += report.filled_base
        self._state.working_base = max(self._state.working_base - report.filled_base, 0)

    def on_reject(self, report: ExecutionReport) -> None:
        # Rejected quantity returns to the parent's remaining pool; the next
        # slice picks it up through the cumulative-target rule.
        self._state.working_base = max(self._state.working_base - report.remaining_base, 0)
        self._state.rejected_base += report.remaining_base

    def on_cancel(self, report: ExecutionReport) -> None:
        self._state.working_base = max(self._state.working_base - report.remaining_base, 0)
        self._state.cancelled_base += report.remaining_base

    # ------------------------------------------------------------ emitting

    def _emit(
        self,
        *,
        state: MarketState,
        quantity_base: int,
        slice_index: int,
        style: PlacementStyle | None = None,
        time_in_force: TimeInForce = TimeInForce.DAY,
    ) -> ChildOrder | None:
        qty = quantity_base
        if self._round_to_lot:
            qty = self._spec.round_to_lot(qty)
        if qty <= 0:
            return None
        self._spec.validate_quantity(qty)
        order = self._oms.create_child(
            symbol=self._parent.symbol,
            side=self._parent.side,
            quantity_base=qty,
            created_ns=state.timestamp_ns,
            parent_order_id=self._parent.parent_order_id,
            slice_index=slice_index,
            style=style or self._style,
            order_type=OrderType.LIMIT,
            time_in_force=time_in_force,
        )
        self._state.working_base += qty
        self._state.slices_emitted += 1
        return order

    def _aggressive(
        self, *, state: MarketState, quantity_base: int, slice_index: int
    ) -> ChildOrder | None:
        return self._emit(
            state=state,
            quantity_base=quantity_base,
            slice_index=slice_index,
            style=PlacementStyle.AGGRESSIVE,
            time_in_force=TimeInForce.IOC,
        )


@dataclass(slots=True)
class Schedule:
    """Absolute (time, cumulative_target) schedule."""

    slice_times_ns: list[int] = field(default_factory=list)
    cumulative_targets: list[int] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.slice_times_ns)


class ScheduledPolicy(ExecutionPolicyBase):
    """TWAP / VWAP / IS baselines: emit against a pre-computed schedule."""

    def __init__(self, *, n_slices: int, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        if n_slices <= 0:
            raise ValueError("n_slices must be positive")
        self._n_slices = n_slices
        self._schedule: Schedule | None = None
        self._next_index = 0

    @property
    def n_slices(self) -> int:
        return self._n_slices

    @property
    def schedule(self) -> Schedule:
        if self._schedule is None:
            self._schedule = self.build_schedule()
        return self._schedule

    @abstractmethod
    def build_schedule(self) -> Schedule: ...

    def on_market_event(self, state: MarketState) -> list[ChildOrder]:
        """Emit every slice whose time has arrived.

        Slice times are strictly inside the window (`< end_ns`). The slice that
        would land exactly on `end_ns` is deliberately not emitted: the
        end-of-window sweep covers that quantity, and emitting both would
        double-count it.
        """
        schedule = self.schedule
        orders: list[ChildOrder] = []
        while self._next_index < len(schedule):
            due_ns = schedule.slice_times_ns[self._next_index]
            if state.timestamp_ns < due_ns or due_ns >= self._parent.end_ns:
                break
            index = self._next_index
            self._next_index += 1
            target = schedule.cumulative_targets[index]
            outstanding = self._state.filled_base + self._state.working_base
            due = min(target - outstanding, self.parent_remaining_base)
            if due > 0:
                order = self._emit(state=state, quantity_base=due, slice_index=index)
                if order is not None:
                    orders.append(order)
        return orders

    def on_window_end(self, state: MarketState) -> list[ChildOrder]:
        """`sweep_marketable` crosses with everything still unfilled."""
        if self._finished or self._end_of_window != "sweep_marketable":
            self._finished = True
            return []
        self._finished = True
        remaining = self.unfilled_base
        if remaining <= 0:
            return []
        order = self._aggressive(state=state, quantity_base=remaining, slice_index=self._n_slices)
        return [order] if order else []

    def _slice_times(self) -> list[int]:
        start = self._parent.start_ns
        duration = self._parent.duration_ns
        return [start + round(duration * (i + 1) / self._n_slices) for i in range(self._n_slices)]

    def _cumulative_from_weights(self, weights: list[float]) -> list[int]:
        total = self._parent.quantity_base
        cumulative: list[int] = []
        running = 0.0
        for weight in weights:
            running += weight
            cumulative.append(round(total * running))
        if cumulative:
            cumulative[-1] = total
        return cumulative
