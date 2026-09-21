"""Approximate queue model for L2 / MBP data.

Read `docs/adr/003-l2-queue-approximation.md` first. Summary:

  * everything displayed at our price when we join is AHEAD of us;
  * new orders join behind us (safe);
  * trades consume from the FRONT (safe);
  * a level shrinking without a trade is UNAtTRIBUTABLE -> policy decides
    whether the removed shares were ahead of us or behind us.

Every quantity this model produces is an ESTIMATE. Names, columns and reports
say so.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import CancelAheadPolicy, QueueMode, Side


@dataclass(slots=True)
class QueuePosition:
    client_order_id: str
    side: Side
    price_ticks: int
    remaining_base: int
    estimated_queue_ahead_base: int
    join_ns: int


class ApproximateQueueModel:
    """Queue estimates from aggregated level data. Mode = APPROXIMATE."""

    def __init__(self, policy: CancelAheadPolicy = CancelAheadPolicy.NEUTRAL) -> None:
        self._policy = policy
        self._positions: dict[str, QueuePosition] = {}
        self._order: list[str] = []

    @property
    def mode(self) -> QueueMode:
        return QueueMode.APPROXIMATE

    @property
    def policy(self) -> CancelAheadPolicy:
        return self._policy

    def register(
        self,
        client_order_id: str,
        *,
        side: Side,
        price_ticks: int,
        quantity_base: int,
        level_size_base: int,
    ) -> None:
        if client_order_id in self._positions:
            raise ValueError(f"order {client_order_id} already registered")
        self._positions[client_order_id] = QueuePosition(
            client_order_id=client_order_id,
            side=side,
            price_ticks=price_ticks,
            remaining_base=quantity_base,
            # We join at the back: all displayed size is ahead of us.
            estimated_queue_ahead_base=max(level_size_base, 0),
            join_ns=0,
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
        """Trades consume the queue from the front, then reach our order.

        `side` is the side of the BOOK that traded. A print against the bid can
        never consume queue from our order resting on the ask, so when `side` is
        supplied we only consider orders resting on that side.
        """
        _ = order_id  # L2 data has no order identity; the quantity is aggregate.
        remaining_trade = quantity_base
        fills: list[tuple[str, int]] = []
        for client_order_id in self._positions_at(price_ticks, side):
            if remaining_trade <= 0:
                break
            position = self._positions[client_order_id]
            if position.estimated_queue_ahead_base > 0:
                consumed = min(position.estimated_queue_ahead_base, remaining_trade)
                position.estimated_queue_ahead_base -= consumed
                remaining_trade -= consumed
                if remaining_trade <= 0:
                    break
            if position.estimated_queue_ahead_base == 0 and remaining_trade > 0:
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
        """Attribute an unattributable shrink using the configured policy."""
        _ = order_id
        for client_order_id in self._positions_at(price_ticks, side):
            position = self._positions[client_order_id]
            if position.estimated_queue_ahead_base <= 0:
                continue
            if self._policy is CancelAheadPolicy.OPTIMISTIC:
                attributed = 0.0
            elif self._policy is CancelAheadPolicy.CONSERVATIVE:
                attributed = float(removed_base)
            else:
                if level_size_before <= 0:
                    attributed = 0.0
                else:
                    share = position.estimated_queue_ahead_base / level_size_before
                    attributed = removed_base * min(max(share, 0.0), 1.0)
            reduction = min(int(attributed), position.estimated_queue_ahead_base)
            position.estimated_queue_ahead_base -= reduction

    def release(self, client_order_id: str) -> None:
        self._positions.pop(client_order_id, None)
        if client_order_id in self._order:
            self._order.remove(client_order_id)

    def queue_ahead_base(self, client_order_id: str) -> int:
        position = self._positions.get(client_order_id)
        if position is None:
            raise KeyError(f"unknown order {client_order_id}")
        return position.estimated_queue_ahead_base

    def fillable_base(self, client_order_id: str) -> int:
        """Approximate model has no standing fill state.

        Fills are produced by `on_trade`: only a trade can consume the queue.
        Price touch alone never fills, so this is always 0.
        """
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
