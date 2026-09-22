"""Market-by-order (MBO / L3) book with exact FIFO queue.

Only constructible when the data source provides order identity. Here FIFO queue
position is *observed*, not estimated: the queue ahead is the sum of quantities
of orders that arrived earlier at the same price.

If the source does not provide order ids, use `MbpBook` and the approximate
queue model instead - never this class.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import NoReturn

from ..domain.book import BookSnapshot, PriceLevel
from ..domain.enums import DataType, EventType, Side
from ..domain.events import MarketEvent
from ..domain.exceptions import BookIntegrityError, DataCapabilityError
from ..domain.instrument import InstrumentSpec
from .config import BookSettings
from .invariants import InvariantViolation, ViolationHook
from .levels import SideLevels


@dataclass(slots=True)
class RestingOrder:
    """One order inside a price level, in arrival order."""

    order_id: int
    quantity_base: int
    arrival_ns: int
    sequence_id: int


class MboBook:
    """Order-level book. Exact queue position is available."""

    def __init__(
        self,
        symbol: str,
        settings: BookSettings,
        declared_data_type: DataType,
        spec: InstrumentSpec | None = None,
        on_violation: ViolationHook | None = None,
    ) -> None:
        if declared_data_type is not DataType.L3_MBO:
            raise DataCapabilityError(
                "MboBook requires L3_MBO data; "
                f"source declares {declared_data_type.value}. "
                "Exact FIFO queue is not observable from L2/MBP."
            )
        self._symbol = symbol
        self._settings = settings
        self._spec = spec
        self._on_violation = on_violation
        self._queues: dict[Side, dict[int, deque[RestingOrder]]] = {
            Side.BUY: {},
            Side.SELL: {},
        }
        # Aggregated mirror used only for fast snapshots / depth.
        self._aggregates: dict[Side, SideLevels] = {
            Side.BUY: SideLevels(is_bid=True, max_levels=settings.max_levels_per_side),
            Side.SELL: SideLevels(is_bid=False, max_levels=settings.max_levels_per_side),
        }
        self._index: dict[int, tuple[Side, int]] = {}
        self._last_sequence_id = -1
        self._last_timestamp_ns = 0
        self._update_count = 0

    # ----------------------------------------------------------- properties

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def last_sequence_id(self) -> int:
        return self._last_sequence_id

    @property
    def best_bid_ticks(self) -> int | None:
        return self._aggregates[Side.BUY].best_price()

    @property
    def best_ask_ticks(self) -> int | None:
        return self._aggregates[Side.SELL].best_price()

    # ---------------------------------------------------------------- apply

    def apply(self, event: MarketEvent) -> None:
        if event.symbol != self._symbol:
            raise BookIntegrityError(f"symbol mismatch: book={self._symbol} event={event.symbol}")
        self._last_sequence_id = event.sequence_id
        self._last_timestamp_ns = event.exchange_timestamp_ns
        if not event.event_type.mutates_book:
            return
        self._update_count += 1

        if event.event_type is EventType.CLEAR:
            for side in (Side.BUY, Side.SELL):
                self._queues[side].clear()
                self._aggregates[side].clear()
            self._index.clear()
            return

        if event.price_ticks is None or event.quantity_base is None or event.side is None:
            raise BookIntegrityError(
                f"{event.event_type.value} requires side/price/quantity (seq={event.sequence_id})"
            )

        if event.event_type is EventType.ADD:
            self._add(event)
        elif event.event_type is EventType.CANCEL:
            self._cancel(event)
        elif event.event_type is EventType.MODIFY:
            self._modify(event)
        elif event.event_type is EventType.REPLACE:
            self._cancel(event)
            self._add(event)
        elif event.event_type is EventType.TRADE:
            self._execute(event)
        elif event.event_type is EventType.SNAPSHOT:
            raise BookIntegrityError(
                "SNAPSHOT is not meaningful for an MBO book; rebuild from order events"
            )

    # --------------------------------------------------------------- queue

    def queue_ahead_base(self, order_id: int) -> int:
        """Exact shares ahead of `order_id` in its price level (FIFO)."""
        if order_id not in self._index:
            raise BookIntegrityError(f"unknown order id {order_id}")
        side, price = self._index[order_id]
        ahead = 0
        for resting in self._queues[side][price]:
            if resting.order_id == order_id:
                return ahead
            ahead += resting.quantity_base
        raise BookIntegrityError(f"order {order_id} missing from its level queue")

    def orders_at(self, side: Side, price_ticks: int) -> tuple[RestingOrder, ...]:
        return tuple(self._queues[side].get(price_ticks, deque()))

    def level_size_base(self, side: Side, price_ticks: int) -> int:
        """Aggregated displayed size at one price."""
        return self._aggregates[side].quantity_at(price_ticks)

    # ------------------------------------------------------------- snapshot

    def snapshot(self, depth: int | None = None) -> BookSnapshot:
        levels = depth if depth is not None else self._settings.snapshot_depth
        bids = self._aggregates[Side.BUY].levels_best_first(levels)
        asks = self._aggregates[Side.SELL].levels_best_first(levels)
        return BookSnapshot(
            timestamp_ns=self._last_timestamp_ns,
            sequence_id=self._last_sequence_id,
            symbol=self._symbol,
            bids=tuple(self._annotate(Side.BUY, bids)),
            asks=tuple(self._annotate(Side.SELL, asks)),
        )

    def _annotate(self, side: Side, levels: tuple[PriceLevel, ...]) -> list[PriceLevel]:
        out: list[PriceLevel] = []
        for level in levels:
            queue = self._queues[side].get(level.price_ticks, deque())
            out.append(
                PriceLevel(
                    price_ticks=level.price_ticks,
                    quantity_base=level.quantity_base,
                    order_count=len(queue),
                )
            )
        return out

    # ------------------------------------------------------------- internal

    # These three helpers exist so the type checker can see the invariants the
    # runtime already enforces. The previous code used bare `assert` statements,
    # which have two problems: `python -O` strips them, so a malformed event
    # would silently corrupt the book in an optimised interpreter; and they
    # narrowed only the two fields they mentioned, leaving `quantity_base` typed
    # `int | None` everywhere downstream.

    @staticmethod
    def _require_side_price_quantity(event: MarketEvent) -> tuple[Side, int, int]:
        side = event.side
        price = event.price_ticks
        quantity = event.quantity_base
        if side is None or price is None or quantity is None:
            raise BookIntegrityError(
                f"{event.event_type.value} event missing side/price/quantity "
                f"(seq={event.sequence_id})"
            )
        return side, price, quantity

    @staticmethod
    def _require_order_id(event: MarketEvent) -> int:
        order_id = event.order_id
        if order_id is None:
            raise BookIntegrityError(
                f"MBO {event.event_type.value} requires order_id (seq={event.sequence_id})"
            )
        return order_id

    def _add(self, event: MarketEvent) -> None:
        side, price, quantity = self._require_side_price_quantity(event)
        order_id = self._require_order_id(event)
        if order_id in self._index:
            self._fail(
                InvariantViolation.DUPLICATE_ORDER_ID,
                f"duplicate order id {order_id} (seq={event.sequence_id})",
                event,
            )
        queue = self._queues[side].setdefault(price, deque())
        queue.append(
            RestingOrder(
                order_id=order_id,
                quantity_base=quantity,
                arrival_ns=event.exchange_timestamp_ns,
                sequence_id=event.sequence_id,
            )
        )
        self._index[order_id] = (side, price)
        self._aggregates[side].add(price, quantity)

    def _cancel(self, event: MarketEvent) -> None:
        _, _, quantity = self._require_side_price_quantity(event)
        order_id = self._require_order_id(event)
        located = self._index.get(order_id)
        if located is None:
            self._fail(
                InvariantViolation.UNKNOWN_ORDER_ID,
                f"cancel for unknown order id {order_id} (seq={event.sequence_id})",
                event,
            )
        side, price = located
        queue = self._queues[side][price]
        target = self._find(queue, order_id)
        if target.quantity_base < quantity:
            raise BookIntegrityError(f"cancel {quantity} exceeds remaining {target.quantity_base}")
        target.quantity_base -= quantity
        self._aggregates[side].remove(price, quantity)
        if target.quantity_base == 0:
            queue.remove(target)
            del self._index[order_id]
            if not queue:
                del self._queues[side][price]

    def _modify(self, event: MarketEvent) -> None:
        """Absolute quantity change. Priority is per `modify_priority_policy`."""
        _, _, quantity = self._require_side_price_quantity(event)
        order_id = self._require_order_id(event)
        located = self._index.get(order_id)
        if located is None:
            self._fail(
                InvariantViolation.UNKNOWN_ORDER_ID,
                f"modify for unknown order id {order_id} (seq={event.sequence_id})",
                event,
            )
        side, price = located
        queue = self._queues[side][price]
        target = self._find(queue, order_id)
        delta = quantity - target.quantity_base
        if self._settings.modify_priority_policy == "KEEP_PRIORITY" and delta < 0:
            # Size decrease keeps priority in some venues; share survives.
            self._aggregates[side].remove(price, -delta)
            target.quantity_base = quantity
            return
        # Default: lose priority -> cancel remaining and rejoin at the back.
        self._aggregates[side].remove(price, target.quantity_base)
        queue.remove(target)
        del self._index[order_id]
        if not queue:
            del self._queues[side][price]
        if quantity > 0:
            self._add(event)

    def _execute(self, event: MarketEvent) -> None:
        """Consume liquidity from the front of the resting queue."""
        _, price, quantity = self._require_side_price_quantity(event)
        aggressor = event.aggressor_side() or event.side
        if aggressor is None:
            raise BookIntegrityError(
                f"MBO TRADE requires an aggressor side (seq={event.sequence_id})"
            )
        resting = aggressor.opposite
        queue = self._queues[resting].get(price)
        if queue is None:
            self._fail(
                InvariantViolation.TRADE_EXCEEDS_DEPTH,
                f"execution at empty price {price} (seq={event.sequence_id})",
                event,
            )
        remaining = quantity
        while remaining > 0:
            if not queue:
                self._fail(
                    InvariantViolation.TRADE_EXCEEDS_DEPTH,
                    f"execution of {quantity} at {price} exhausts the resting queue "
                    f"with {remaining} unfilled (seq={event.sequence_id})",
                    event,
                )
            front = queue[0]
            taken = min(front.quantity_base, remaining)
            front.quantity_base -= taken
            self._aggregates[resting].remove(price, taken)
            remaining -= taken
            if front.quantity_base == 0:
                queue.popleft()
                self._index.pop(front.order_id, None)
                if not queue:
                    del self._queues[resting][price]

    @staticmethod
    def _find(queue: deque[RestingOrder], order_id: int) -> RestingOrder:
        """The named order within a level. Raises rather than returning None."""
        for order in queue:
            if order.order_id == order_id:
                return order
        raise BookIntegrityError(f"order {order_id} not present in its level")

    def _fail(self, violation: InvariantViolation, message: str, event: MarketEvent) -> NoReturn:
        """Report through the shared hook, then raise.

        An order-level book cannot continue past an integrity failure - an
        unknown order id means the reconstructed queue is already wrong, and
        guessing would corrupt every subsequent fill. So the hook is notified
        and the error is raised; unlike the MBP book there is no WARN mode that
        could meaningfully carry on. What matters is that the violation is
        *recorded*, which it previously was not: `on_violation` was accepted by
        the constructor and never called, so MBO violations were invisible in
        `outcome.book_violations`.
        """
        if self._on_violation is not None:
            self._on_violation(violation, message, event)
        raise BookIntegrityError(f"{violation.value}: {message}")
