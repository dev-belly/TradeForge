"""Research-grade order management.

Responsibilities only: id assignment, state transitions, bookkeeping. It does
not know about the book, the queue model or the clock - the simulator owns
those. Illegal transitions raise instead of being silently applied.
"""

from __future__ import annotations

from decimal import Decimal

from ..domain.enums import (
    LiquidityFlag,
    OrderStatus,
    OrderType,
    ReportType,
    Side,
    TimeInForce,
)
from ..domain.exceptions import OrderStateError
from ..domain.fills import ExecutionReport, Fill
from ..domain.orders import ChildOrder


class Oms:
    """Order state machine + inventory bookkeeping."""

    def __init__(self) -> None:
        self._next_order_id = 1
        self._next_fill_id = 1
        self._orders: dict[str, ChildOrder] = {}
        self._working: list[str] = []
        self._inventory_base = 0
        self._fills: list[Fill] = []

    # ------------------------------------------------------------- creation

    def create_child(
        self,
        *,
        symbol: str,
        side: Side,
        quantity_base: int,
        created_ns: int,
        parent_order_id: str,
        slice_index: int,
        style: object,
        order_type: OrderType = OrderType.LIMIT,
        time_in_force: TimeInForce = TimeInForce.DAY,
        price_ticks: int | None = None,
    ) -> ChildOrder:
        order_id = self._next_order_id
        self._next_order_id += 1
        client_order_id = f"{parent_order_id}-{slice_index:04d}-{order_id:06d}"
        from ..domain.enums import PlacementStyle

        order = ChildOrder(
            order_id=order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity_base=quantity_base,
            time_in_force=time_in_force,
            created_ns=created_ns,
            price_ticks=price_ticks,
            parent_order_id=parent_order_id,
            slice_index=slice_index,
            style=PlacementStyle(str(getattr(style, "value", style))),
        )
        self._orders[client_order_id] = order
        return order

    # ------------------------------------------------------------ lifecycle

    def accept(self, order: ChildOrder, *, at_ns: int) -> ExecutionReport:
        order.transition_to(OrderStatus.ACCEPTED, at_ns=at_ns)
        if order.client_order_id not in self._working:
            self._working.append(order.client_order_id)
        return ExecutionReport(
            report_type=ReportType.ACCEPTED,
            client_order_id=order.client_order_id,
            order_id=order.order_id,
            parent_order_id=order.parent_order_id,
            timestamp_ns=at_ns,
            status=order.status,
            remaining_base=order.remaining_base,
        )

    def fill(
        self,
        order: ChildOrder,
        *,
        quantity_base: int,
        price_ticks: int,
        at_ns: int,
        liquidity: LiquidityFlag,
        fee: Decimal,
        sequence_id: int = 0,
        queue_wait_ns: int = 0,
    ) -> ExecutionReport:
        if order.status not in (OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED):
            raise OrderStateError(
                f"cannot fill order in state {order.status.value} ({order.client_order_id})"
            )
        fill = Fill(
            fill_id=self._next_fill_id,
            order_id=order.order_id,
            client_order_id=order.client_order_id,
            parent_order_id=order.parent_order_id,
            symbol=order.symbol,
            side=order.side,
            price_ticks=price_ticks,
            quantity_base=quantity_base,
            timestamp_ns=at_ns,
            liquidity_flag=liquidity,
            fee=fee,
            queue_wait_ns=queue_wait_ns,
            sequence_id=sequence_id,
        )
        self._next_fill_id += 1
        self._fills.append(fill)
        order.apply_fill(quantity_base, at_ns=at_ns)
        self._inventory_base += order.side.sign * quantity_base
        if order.status is OrderStatus.FILLED and order.client_order_id in self._working:
            self._working.remove(order.client_order_id)
        return ExecutionReport(
            report_type=(
                ReportType.FILL if order.status is OrderStatus.FILLED else ReportType.PARTIAL_FILL
            ),
            client_order_id=order.client_order_id,
            order_id=order.order_id,
            parent_order_id=order.parent_order_id,
            timestamp_ns=at_ns,
            status=order.status,
            filled_base=quantity_base,
            remaining_base=order.remaining_base,
            fill=fill,
        )

    def cancel_pending(self, order: ChildOrder, *, at_ns: int) -> None:
        order.transition_to(OrderStatus.CANCEL_PENDING, at_ns=at_ns)

    def cancel(self, order: ChildOrder, *, at_ns: int, reason: str = "") -> ExecutionReport:
        if order.status in (
            OrderStatus.NEW,
            OrderStatus.CANCEL_PENDING,
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
        ):
            order.transition_to(OrderStatus.CANCELLED, at_ns=at_ns)
        elif not order.status.is_terminal:
            raise OrderStateError(f"cannot cancel order in state {order.status.value}")
        if order.client_order_id in self._working:
            self._working.remove(order.client_order_id)
        return ExecutionReport(
            report_type=ReportType.CANCELLED,
            client_order_id=order.client_order_id,
            order_id=order.order_id,
            parent_order_id=order.parent_order_id,
            timestamp_ns=at_ns,
            status=order.status,
            remaining_base=order.remaining_base,
            reason=reason,
        )

    def expire(self, order: ChildOrder, *, at_ns: int) -> ExecutionReport:
        order.transition_to(OrderStatus.EXPIRED, at_ns=at_ns)
        if order.client_order_id in self._working:
            self._working.remove(order.client_order_id)
        return ExecutionReport(
            report_type=ReportType.EXPIRED,
            client_order_id=order.client_order_id,
            order_id=order.order_id,
            parent_order_id=order.parent_order_id,
            timestamp_ns=at_ns,
            status=order.status,
            remaining_base=order.remaining_base,
            reason="time in force expired",
        )

    def reject(self, order: ChildOrder, *, at_ns: int, reason: str) -> ExecutionReport:
        order.transition_to(OrderStatus.REJECTED, at_ns=at_ns)
        return ExecutionReport(
            report_type=ReportType.REJECTED,
            client_order_id=order.client_order_id,
            order_id=order.order_id,
            parent_order_id=order.parent_order_id,
            timestamp_ns=at_ns,
            status=order.status,
            remaining_base=order.remaining_base,
            reason=reason,
        )

    # ------------------------------------------------------------- accessors

    @property
    def open_orders(self) -> tuple[ChildOrder, ...]:
        return tuple(self._orders[cid] for cid in self._working if cid in self._orders)

    @property
    def n_open_orders(self) -> int:
        return len(self._working)

    @property
    def inventory_base(self) -> int:
        return self._inventory_base

    @property
    def fills(self) -> tuple[Fill, ...]:
        return tuple(self._fills)

    def order(self, client_order_id: str) -> ChildOrder:
        return self._orders[client_order_id]

    def all_orders(self) -> tuple[ChildOrder, ...]:
        return tuple(self._orders.values())
