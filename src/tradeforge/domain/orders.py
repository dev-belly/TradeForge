"""Orders: parent orders, child orders, and the legal state machine.

Status is never assigned ad hoc: `transition_to` validates against
`ALLOWED_ORDER_TRANSITIONS` and raises `OrderStateError` otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .enums import (
    OrderStatus,
    OrderType,
    PlacementStyle,
    Side,
    TimeInForce,
)
from .exceptions import OrderStateError

ALLOWED_ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.NEW: frozenset({OrderStatus.ACCEPTED, OrderStatus.REJECTED}),
    OrderStatus.ACCEPTED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCEL_PENDING,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCEL_PENDING,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
        }
    ),
    OrderStatus.CANCEL_PENDING: frozenset(
        {
            OrderStatus.CANCELLED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
        }
    ),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}


@dataclass(slots=True)
class Order:
    """A single order in the research OMS."""

    order_id: int
    client_order_id: str
    symbol: str
    side: Side
    order_type: OrderType
    quantity_base: int
    time_in_force: TimeInForce
    created_ns: int
    price_ticks: int | None = None
    status: OrderStatus = OrderStatus.NEW
    filled_base: int = 0
    working_ns: int | None = None
    terminal_ns: int | None = None

    @property
    def remaining_base(self) -> int:
        return self.quantity_base - self.filled_base

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal

    @property
    def is_marketable(self) -> bool:
        """A market order, or a limit order priced at/through the touch.

        The touch is supplied by the caller: the order itself does not know the
        book (which is deliberate - orders never hold market state).
        """
        return self.order_type is OrderType.MARKET

    def transition_to(self, new_status: OrderStatus, *, at_ns: int) -> None:
        allowed = ALLOWED_ORDER_TRANSITIONS.get(self.status, frozenset())
        if new_status not in allowed:
            raise OrderStateError(
                f"illegal order transition {self.status.value} -> {new_status.value} "
                f"for order {self.client_order_id}"
            )
        self.status = new_status
        if new_status is OrderStatus.ACCEPTED:
            self.working_ns = at_ns
        if new_status.is_terminal:
            self.terminal_ns = at_ns

    def apply_fill(self, quantity_base: int, *, at_ns: int) -> None:
        """Apply a (partial) fill and move the state machine accordingly."""
        if quantity_base <= 0:
            raise OrderStateError("fill quantity must be positive")
        if quantity_base > self.remaining_base:
            raise OrderStateError(
                f"fill {quantity_base} exceeds remaining {self.remaining_base} "
                f"for order {self.client_order_id}"
            )
        self.filled_base += quantity_base
        if self.filled_base == self.quantity_base:
            self.transition_to(OrderStatus.FILLED, at_ns=at_ns)
        else:
            self.transition_to(OrderStatus.PARTIALLY_FILLED, at_ns=at_ns)


@dataclass(slots=True)
class ChildOrder(Order):
    """A child order belonging to a parent execution.

    `style` decides whether the simulator posts passively (queue risk) or
    crosses the spread (certain, but pays spread + fees).
    """

    parent_order_id: str = ""
    slice_index: int = -1
    style: PlacementStyle = PlacementStyle.AGGRESSIVE
    # Passive placement bookkeeping (see queue model).
    placed_price_ticks: int | None = None
    estimated_queue_ahead_base: int = 0
    queue_join_ns: int | None = None


@dataclass(slots=True)
class ParentOrder:
    """The institutional order we are trying to execute."""

    parent_order_id: str
    symbol: str
    side: Side
    quantity_base: int
    start_ns: int
    end_ns: int
    urgency: float = 0.5
    participation_limit: float = 0.05
    limit_price_ticks: int | None = None
    filled_base: int = 0
    child_ids: list[str] = field(default_factory=list)

    @property
    def remaining_base(self) -> int:
        return self.quantity_base - self.filled_base

    @property
    def duration_ns(self) -> int:
        return self.end_ns - self.start_ns

    @property
    def completion_rate(self) -> float:
        if self.quantity_base == 0:
            return 0.0
        return self.filled_base / self.quantity_base
