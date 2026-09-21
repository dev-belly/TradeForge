"""Exact FIFO queue model for L3 / MBO data.

Only valid when the source provides order identity. Here "queue ahead" is not an
estimate: we track the individual resting orders that are ahead of ours, and
every cancel / execution event tells us *which* order it refers to.

Constructing this with L2 data is a capability violation and raises.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..domain.capability import Capability, require
from ..domain.enums import DataType, QueueMode, Side
from ..domain.exceptions import DataCapabilityError


@dataclass(slots=True)
class _AheadOrder:
    order_id: int
    quantity_base: int


@dataclass(slots=True)
class ExactPosition:
    client_order_id: str
    side: Side
    price_ticks: int
    remaining_base: int
    join_ns: int
    ahead: list[_AheadOrder] = field(default_factory=list)

    @property
    def queue_ahead_base(self) -> int:
        return sum(order.quantity_base for order in self.ahead)


class ExactQueueModel:
    """Exact queue tracking. Mode = EXACT."""

    def __init__(self, data_type: DataType) -> None:
        require(data_type, Capability.EXACT_QUEUE, context="ExactQueueModel")
        self._data_type = data_type
        self._positions: dict[str, ExactPosition] = {}
        self._order: list[str] = []

    @property
    def mode(self) -> QueueMode:
        return QueueMode.EXACT

    @property
    def data_type(self) -> DataType:
        return self._data_type

    def register(
        self,
        client_order_id: str,
        *,
        side: Side,
        price_ticks: int,
        quantity_base: int,
        level_size_base: int,
    ) -> None:
        """`level_size_base` is unused here: the caller passes the resting orders.

        Use `register_with_queue` when the book can enumerate the orders ahead.
        """
        _ = level_size_base
        self._positions[client_order_id] = ExactPosition(
            client_order_id=client_order_id,
            side=side,
            price_ticks=price_ticks,
            remaining_base=quantity_base,
            join_ns=0,
        )
        self._order.append(client_order_id)

    def register_with_queue(
        self,
        client_order_id: str,
        *,
        side: Side,
        price_ticks: int,
        quantity_base: int,
        orders_ahead: list[tuple[int, int]],
    ) -> None:
        """Register knowing exactly which orders (id, qty) are ahead of us."""
        self._positions[client_order_id] = ExactPosition(
            client_order_id=client_order_id,
            side=side,
            price_ticks=price_ticks,
            remaining_base=quantity_base,
            join_ns=0,
            ahead=[_AheadOrder(order_id=oid, quantity_base=qty) for oid, qty in orders_ahead],
        )
        self._order.append(client_order_id)

    def on_trade(
        self,
        *,
        price_ticks: int,
        quantity_base: int,
        order_id: int | None = None,
        side: Side | None = None,
    ) -> list[tuple[str, int]]:
        remaining_trade = quantity_base
        fills: list[tuple[str, int]] = []
        for client_order_id in self._positions_at(price_ticks, side):
            if remaining_trade <= 0:
                break
            position = self._positions[client_order_id]
            if order_id is not None:
                # MBO names the executed order. Under price-time priority an
                # execution always consumes from the front, so:
                #   * order ahead of us  -> it shrinks, we move up;
                #   * no orders ahead    -> the execution reaches us;
                #   * order behind us    -> impossible under FIFO; ignore.
                matched = next((o for o in position.ahead if o.order_id == order_id), None)
                if matched is not None:
                    taken = min(matched.quantity_base, remaining_trade)
                    matched.quantity_base -= taken
                    remaining_trade -= taken
                    if matched.quantity_base <= 0:
                        position.ahead.remove(matched)
                    continue
                if position.ahead:
                    continue
                filled = min(position.remaining_base, remaining_trade)
                position.remaining_base -= filled
                remaining_trade -= filled
                fills.append((client_order_id, filled))
                continue
            while position.ahead and remaining_trade > 0:
                front = position.ahead[0]
                taken = min(front.quantity_base, remaining_trade)
                front.quantity_base -= taken
                remaining_trade -= taken
                if front.quantity_base <= 0:
                    position.ahead.pop(0)
            if not position.ahead and remaining_trade > 0:
                filled = min(position.remaining_base, remaining_trade)
                position.remaining_base -= filled
                remaining_trade -= filled
                fills.append((client_order_id, filled))
        self._drop_empty()
        return fills

    def on_size_decrease(
        self,
        *,
        price_ticks: int,
        removed_base: int,
        level_size_before: int,
        order_id: int | None = None,
        side: Side | None = None,
    ) -> None:
        """Exact attribution: the event names the order that shrank."""
        _ = level_size_before
        if order_id is None:
            raise DataCapabilityError(
                "exact queue requires order identity in cancel events; got order_id=None"
            )
        for client_order_id in self._positions_at(price_ticks, side):
            position = self._positions[client_order_id]
            for order in position.ahead:
                if order.order_id == order_id:
                    order.quantity_base = max(order.quantity_base - removed_base, 0)
                    break
            position.ahead = [o for o in position.ahead if o.quantity_base > 0]

    def release(self, client_order_id: str) -> None:
        self._positions.pop(client_order_id, None)
        if client_order_id in self._order:
            self._order.remove(client_order_id)

    def queue_ahead_base(self, client_order_id: str) -> int:
        position = self._positions.get(client_order_id)
        if position is None:
            raise KeyError(f"unknown order {client_order_id}")
        return position.queue_ahead_base

    def fillable_base(self, client_order_id: str) -> int:
        """Exact model has no standing fill state; fills come from `on_trade`."""
        _ = client_order_id
        return 0

    def remaining_base(self, client_order_id: str) -> int:
        position = self._positions.get(client_order_id)
        if position is None:
            raise KeyError(f"unknown order {client_order_id}")
        return position.remaining_base

    # ------------------------------------------------------------- internal

    def _positions_at(self, price_ticks: int, side: Side | None = None) -> list[str]:
        return [
            cid
            for cid in self._order
            if cid in self._positions
            and self._positions[cid].price_ticks == price_ticks
            and (side is None or self._positions[cid].side is side)
        ]

    def _drop_empty(self) -> None:
        for cid in [c for c, p in self._positions.items() if p.remaining_base <= 0]:
            self.release(cid)
