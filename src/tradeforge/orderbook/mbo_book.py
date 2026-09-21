"""Market-by-order (MBO / L3) book with exact FIFO queue.

Only constructible when the data source provides order identity. Here FIFO queue
position is *observed*, not estimated: the queue ahead is the sum of quantities
of orders that arrived earlier at the same price.

If the source does not provide order ids, use `MbpBook` and the approximate
queue model instead - never this class.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from ..domain.book import BookSnapshot, PriceLevel
from ..domain.enums import DataType, EventType, Side
from ..domain.events import MarketEvent
from ..domain.exceptions import BookIntegrityError, DataCapabilityError
from ..domain.instrument import InstrumentSpec
from .config import BookSettings
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
        on_violation: Callable[[str, str], None] | None = None,
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

    def _add(self, event: MarketEvent) -> None:
        assert event.side is not None and event.price_ticks is not None
        if event.order_id is None:
            raise BookIntegrityError(f"MBO ADD requires order_id (seq={event.sequence_id})")
        if event.order_id in self._index:
            raise BookIntegrityError(f"duplicate order id {event.order_id}")
        queue = self._queues[event.side].setdefault(event.price_ticks, deque())
        queue.append(
            RestingOrder(
                order_id=event.order_id,
                quantity_base=event.quantity_base,
                arrival_ns=event.exchange_timestamp_ns,
                sequence_id=event.sequence_id,
            )
        )
        self._index[event.order_id] = (event.side, event.price_ticks)
        self._aggregates[event.side].add(event.price_ticks, event.quantity_base)

    def _cancel(self, event: MarketEvent) -> None:
        assert event.side is not None and event.price_ticks is not None
        if event.order_id is None:
            raise BookIntegrityError(f"MBO CANCEL requires order_id (seq={event.sequence_id})")
        located = self._index.get(event.order_id)
        if located is None:
            raise BookIntegrityError(
                f"cancel for unknown order id {event.order_id} (seq={event.sequence_id})"
            )
        side, price = located
        queue = self._queues[side][price]
        target = next((o for o in queue if o.order_id == event.order_id), None)
        if target is None:
            raise BookIntegrityError(f"order {event.order_id} not present in level")
        if target.quantity_base < event.quantity_base:
            raise BookIntegrityError(
                f"cancel {event.quantity_base} exceeds remaining {target.quantity_base}"
            )
        target.quantity_base -= event.quantity_base
        self._aggregates[side].remove(price, event.quantity_base)
        if target.quantity_base == 0:
            queue.remove(target)
            del self._index[event.order_id]
            if not queue:
                del self._queues[side][price]

    def _modify(self, event: MarketEvent) -> None:
        """Absolute quantity change. Priority is per `modify_priority_policy`."""
        assert event.side is not None and event.price_ticks is not None
        if event.order_id is None:
            raise BookIntegrityError("MBO MODIFY requires order_id")
        located = self._index.get(event.order_id)
        if located is None:
            raise BookIntegrityError(f"modify for unknown order id {event.order_id}")
        side, price = located
        queue = self._queues[side][price]
        target = next((o for o in queue if o.order_id == event.order_id), None)
        if target is None:
            raise BookIntegrityError(f"order {event.order_id} not present in level")
        delta = event.quantity_base - target.quantity_base
        if self._settings.modify_priority_policy == "KEEP_PRIORITY" and delta < 0:
            # Size decrease keeps priority in some venues; share survives.
            self._aggregates[side].remove(price, -delta)
            target.quantity_base = event.quantity_base
            return
        # Default: lose priority -> cancel remaining and rejoin at the back.
        self._aggregates[side].remove(price, target.quantity_base)
        queue.remove(target)
        del self._index[event.order_id]
        if not queue:
            del self._queues[side][price]
        if event.quantity_base > 0:
            self._add(event)

    def _execute(self, event: MarketEvent) -> None:
        """Consume liquidity from the front of the resting queue."""
        assert event.price_ticks is not None
        aggressor = event.aggressor_side() or event.side
        if aggressor is None:
            raise BookIntegrityError(
                f"MBO TRADE requires an aggressor side (seq={event.sequence_id})"
            )
        resting = aggressor.opposite
        queue = self._queues[resting].get(event.price_ticks)
        if queue is None:
            raise BookIntegrityError(
                f"execution at empty price {event.price_ticks} (seq={event.sequence_id})"
            )
        remaining = event.quantity_base
        while remaining > 0:
            front = queue[0]
            taken = min(front.quantity_base, remaining)
            front.quantity_base -= taken
            self._aggregates[resting].remove(event.price_ticks, taken)
            remaining -= taken
            if front.quantity_base == 0:
                queue.popleft()
                self._index.pop(front.order_id, None)
                if not queue:
                    del self._queues[resting][event.price_ticks]
                    break
