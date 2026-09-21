"""Simulated matching against an observable book state.

This is NOT the historical replay path. Replay reconstructs what happened; this
engine answers "what would happen to *our* order if it arrived now".

Design decision (documented limitation): simulated fills do not remove liquidity
from the replayed book. Historical trades already consumed it, and our orders
were not in the historical stream. We therefore price our fills against the
observable book and accept that our own impact on *future* events is not
modelled (replay approximation).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.book import BookSnapshot, PriceLevel
from ..domain.enums import LiquidityFlag, OrderType, Side, TimeInForce
from ..domain.exceptions import MatchingError


@dataclass(frozen=True, slots=True)
class MatchedQuantity:
    price_ticks: int
    quantity_base: int


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Outcome of crossing the book."""

    matched: tuple[MatchedQuantity, ...]
    requested_base: int
    filled_base: int
    remaining_base: int
    liquidity_flag: LiquidityFlag
    levels_crossed: int

    @property
    def avg_price_ticks(self) -> float | None:
        if self.filled_base == 0:
            return None
        notional = sum(m.price_ticks * m.quantity_base for m in self.matched)
        return notional / self.filled_base

    @property
    def worst_price_ticks(self) -> int | None:
        return self.matched[-1].price_ticks if self.matched else None


def is_marketable(snapshot: BookSnapshot, side: Side, price_ticks: int | None) -> bool:
    """A limit order is marketable when it is at or through the touch."""
    if price_ticks is None:
        return True
    if side is Side.BUY:
        return snapshot.best_ask_ticks is not None and price_ticks >= snapshot.best_ask_ticks
    return snapshot.best_bid_ticks is not None and price_ticks <= snapshot.best_bid_ticks


def match_order(
    snapshot: BookSnapshot,
    *,
    side: Side,
    quantity_base: int,
    order_type: OrderType = OrderType.MARKET,
    limit_price_ticks: int | None = None,
    time_in_force: TimeInForce = TimeInForce.DAY,
    max_levels_crossed: int | None = None,
) -> MatchResult:
    """Cross the book with `quantity_base`.

    Price-time priority is respected implicitly: we walk levels from the touch
    outwards and take the displayed quantity at each level. FIFO within a level
    is not observable from MBP data, which is exactly why passive fills go
    through the queue model instead of this function.

    Market orders never fill at mid: they pay the touch and walk the book.
    """
    if quantity_base <= 0:
        raise MatchingError(f"order quantity must be positive, got {quantity_base}")
    if order_type is OrderType.LIMIT and limit_price_ticks is None:
        raise MatchingError("limit order requires a limit price")

    book = snapshot.asks if side is Side.BUY else snapshot.bids
    if not book:
        return MatchResult(
            matched=(),
            requested_base=quantity_base,
            filled_base=0,
            remaining_base=quantity_base,
            liquidity_flag=LiquidityFlag.TAKER,
            levels_crossed=0,
        )

    matched: list[MatchedQuantity] = []
    remaining = quantity_base
    for level in book:
        if remaining <= 0:
            break
        if max_levels_crossed is not None and len(matched) >= max_levels_crossed:
            break
        if not _price_acceptable(side, level, limit_price_ticks):
            break
        take = min(level.quantity_base, remaining)
        if take <= 0:
            continue
        matched.append(MatchedQuantity(price_ticks=level.price_ticks, quantity_base=take))
        remaining -= take

    filled = quantity_base - remaining
    if time_in_force is TimeInForce.FOK and remaining > 0:
        return MatchResult(
            matched=(),
            requested_base=quantity_base,
            filled_base=0,
            remaining_base=quantity_base,
            liquidity_flag=LiquidityFlag.TAKER,
            levels_crossed=0,
        )
    if time_in_force is TimeInForce.IOC and remaining > 0:
        # IOC keeps what it crossed and cancels the rest.
        return MatchResult(
            matched=tuple(matched),
            requested_base=quantity_base,
            filled_base=filled,
            remaining_base=0,
            liquidity_flag=LiquidityFlag.TAKER,
            levels_crossed=len(matched),
        )
    return MatchResult(
        matched=tuple(matched),
        requested_base=quantity_base,
        filled_base=filled,
        remaining_base=remaining,
        liquidity_flag=LiquidityFlag.TAKER,
        levels_crossed=len(matched),
    )


def _price_acceptable(side: Side, level: PriceLevel, limit_price_ticks: int | None) -> bool:
    if limit_price_ticks is None:
        return True
    if side is Side.BUY:
        return level.price_ticks <= limit_price_ticks
    return level.price_ticks >= limit_price_ticks


def resting_price_ticks(snapshot: BookSnapshot, side: Side, offset_ticks: int = 0) -> int | None:
    """Price at which a passive order would join the queue.

    BUY joins the bid `offset_ticks` below the best bid; SELL joins the ask
    `offset_ticks` above the best ask. Returns None if that side is empty.
    """
    if side is Side.BUY:
        best = snapshot.best_bid_ticks
        return None if best is None else best - offset_ticks
    best = snapshot.best_ask_ticks
    return None if best is None else best + offset_ticks
