"""Deterministic synthetic event generator.

IMPORTANT: the output is SYNTHETIC. It is not market data, it is not
calibrated to any venue, and every report that uses it must say so
(`dataset.provenance` in `configs/market_data.yaml`).

Why it exists:
  * `make demo` must work with zero downloads and zero API keys;
  * tests need reproducible event streams that satisfy every book invariant;
  * it exercises both capability tiers: `mbp` (L2) and `mbo` (L3, with order ids
    so the EXACT queue path can be tested end to end).

The generator maintains its own shadow book, so it only ever emits events that
are valid against that book: no negative depth, no unknown cancels, no trades
against empty levels.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from decimal import Decimal

from ...domain.enums import DataType, EventFlag, EventType, Side
from ...domain.events import MarketEvent
from ...orderbook.levels import SideLevels
from .base import BaseAdapter

_ADD_WEIGHT = 0.55
_CANCEL_WEIGHT = 0.24  # remainder (0.21) is TRADE

# Price offsets are symmetric so the generated mid is an unbiased random walk
# rather than a deterministic drift. A drifting sample would make every
# execution experiment measure the drift instead of the execution logic.
_PRICE_OFFSETS: tuple[int, ...] = (-2, -1, -1, 0, 0, 1, 1, 2)


@dataclass(slots=True)
class _LevelOrder:
    order_id: int
    quantity_base: int


@dataclass(slots=True)
class _ShadowBook:
    """Book kept by the generator so emitted events are always consistent."""

    is_mbo: bool
    bids: SideLevels = field(default_factory=lambda: SideLevels(is_bid=True, max_levels=1000))
    asks: SideLevels = field(default_factory=lambda: SideLevels(is_bid=False, max_levels=1000))
    orders: dict[Side, dict[int, list[_LevelOrder]]] = field(
        default_factory=lambda: {Side.BUY: {}, Side.SELL: {}}
    )
    next_order_id: int = 1

    def add(self, side: Side, price_ticks: int, quantity_base: int) -> int:
        levels = self.bids if side is Side.BUY else self.asks
        levels.add(price_ticks, quantity_base)
        order_id = 0
        if self.is_mbo:
            order_id = self.next_order_id
            self.next_order_id += 1
            self.orders[side].setdefault(price_ticks, []).append(
                _LevelOrder(order_id=order_id, quantity_base=quantity_base)
            )
        return order_id

    def cancel(self, side: Side, price_ticks: int, quantity_base: int) -> tuple[int, int]:
        """Cancel up to `quantity_base`. Returns (order_id, quantity_removed).

        In MBO mode the removable amount is capped by the *front* order: a
        cancel can never take more than the order it refers to.
        """
        levels = self.bids if side is Side.BUY else self.asks
        order_id = 0
        if self.is_mbo:
            queue = self.orders[side][price_ticks]
            target = queue[0]
            order_id = target.order_id
            quantity_base = min(quantity_base, target.quantity_base)
            target.quantity_base -= quantity_base
            if target.quantity_base <= 0:
                queue.pop(0)
                if not queue:
                    del self.orders[side][price_ticks]
        levels.remove(price_ticks, quantity_base)
        return order_id, quantity_base

    def trade(self, aggressor: Side, price_ticks: int, quantity_base: int) -> tuple[int, int]:
        """Execute against the front of the resting queue. Returns (order_id, qty)."""
        resting = aggressor.opposite
        levels = self.bids if resting is Side.BUY else self.asks
        order_id = 0
        if self.is_mbo:
            queue = self.orders[resting][price_ticks]
            front = queue[0]
            order_id = front.order_id
            quantity_base = min(quantity_base, front.quantity_base)
            front.quantity_base -= quantity_base
            if front.quantity_base <= 0:
                queue.pop(0)
                if not queue:
                    del self.orders[resting][price_ticks]
        levels.remove(price_ticks, quantity_base)
        return order_id, quantity_base

    def random_level(self, side: Side, rng: random.Random) -> tuple[int, int] | None:
        levels = self.bids if side is Side.BUY else self.asks
        prices = levels.prices_best_first()
        if not prices:
            return None
        price = prices[rng.randrange(len(prices))]
        return price, levels.quantity_at(price)

    def available_at_best(self, side: Side) -> tuple[int, int] | None:
        levels = self.bids if side is Side.BUY else self.asks
        best = levels.best_price()
        if best is None:
            return None
        return best, levels.quantity_at(best)


class SyntheticAdapter(BaseAdapter):
    """Deterministic synthetic market event stream (L2 or L3)."""

    def __init__(self, options: dict[str, object] | None = None) -> None:
        super().__init__(options)
        self._mode = self.opt_str("mode", "mbp").lower()
        if self._mode not in {"mbp", "mbo"}:
            raise ValueError(f"synthetic mode must be 'mbp' or 'mbo', got {self._mode!r}")
        self._is_mbo = self._mode == "mbo"

    @property
    def name(self) -> str:
        return f"synthetic-{self._mode}"

    @property
    def data_type(self) -> DataType:
        return DataType.L3_MBO if self._is_mbo else DataType.L2_MBP

    def events(self) -> Iterator[MarketEvent]:
        rng = random.Random(self.opt_int("seed", 20260908))
        n_events = self.opt_int("n_events", 20000)
        symbol = self.opt_str("symbol", "SYNTH")
        tick_size = Decimal(self.opt_str("tick_size", "0.01"))
        ref_ticks = int(Decimal(self.opt_str("reference_price", "100.00")) / tick_size)
        levels = self.opt_int("initial_depth_levels", 5)
        level_size = self.opt_int("level_size_base", 300)
        spread_ticks = self.opt_int("initial_spread_ticks", 2)
        t = self.opt_int("start_time_ns", 34_200_000_000_000)
        min_gap = self.opt_int("min_gap_ns", 10_000_000)
        max_gap = self.opt_int("max_gap_ns", 80_000_000)
        max_order = self.opt_int("max_order_qty_base", 220)
        max_trade = self.opt_int("max_trade_qty_base", 220)
        max_book_depth = self.opt_int("max_book_depth_base", 12000)

        book = _ShadowBook(is_mbo=self._is_mbo)
        # The book is built from explicit ADD events at the start timestamp, so
        # a consumer replaying the stream reconstructs exactly the same state.
        half_spread = spread_ticks // 2
        first_seq = -2 * levels
        for level in range(levels):
            distance = half_spread + level + 1
            for side, price in (
                (Side.BUY, ref_ticks - distance),
                (Side.SELL, ref_ticks + distance),
            ):
                order_id = book.add(side, price, level_size)
                yield MarketEvent(
                    sequence_id=first_seq + 2 * level + (0 if side is Side.BUY else 1),
                    exchange_timestamp_ns=t,
                    symbol=symbol,
                    event_type=EventType.ADD,
                    side=side,
                    price_ticks=price,
                    quantity_base=level_size,
                    order_id=order_id if self._is_mbo else None,
                    source=self.name,
                )

        for seq in range(n_events):
            t += rng.randint(min_gap, max_gap)
            roll = rng.random()
            total_depth = book.bids.total_quantity() + book.asks.total_quantity()
            wants_remove = roll >= _ADD_WEIGHT
            if roll < _ADD_WEIGHT and total_depth >= max_book_depth:
                # Liquidity control: once the book is deep enough, additions
                # become removals so depth stays in a realistic range.
                wants_remove = True
            if wants_remove:
                if roll < _ADD_WEIGHT + _CANCEL_WEIGHT:
                    event = self._make_cancel(book, rng, symbol, seq, t, max_order)
                else:
                    event = self._make_trade(book, rng, symbol, seq, t, max_trade)
            else:
                event = self._make_add(book, rng, symbol, seq, t, max_order)
            if event is not None:
                yield event

    # ------------------------------------------------------------- builders

    def _make_add(
        self,
        book: _ShadowBook,
        rng: random.Random,
        symbol: str,
        seq: int,
        t: int,
        max_qty: int,
    ) -> MarketEvent | None:
        side = Side.BUY if rng.random() < 0.5 else Side.SELL
        price = self._pick_add_price(book, side, rng)
        if price is None:
            return None
        qty = rng.randint(1, max_qty)
        order_id = book.add(side, price, qty)
        return MarketEvent(
            sequence_id=seq,
            exchange_timestamp_ns=t,
            symbol=symbol,
            event_type=EventType.ADD,
            side=side,
            price_ticks=price,
            quantity_base=qty,
            order_id=order_id if self._is_mbo else None,
            source=self.name,
        )

    def _make_cancel(
        self,
        book: _ShadowBook,
        rng: random.Random,
        symbol: str,
        seq: int,
        t: int,
        max_qty: int,
    ) -> MarketEvent | None:
        side = Side.BUY if rng.random() < 0.5 else Side.SELL
        chosen = book.random_level(side, rng)
        if chosen is None:
            return None
        price, available = chosen
        requested = min(available, rng.randint(1, max_qty))
        order_id, qty = book.cancel(side, price, requested)
        if qty <= 0:
            return None
        return MarketEvent(
            sequence_id=seq,
            exchange_timestamp_ns=t,
            symbol=symbol,
            event_type=EventType.CANCEL,
            side=side,
            price_ticks=price,
            quantity_base=qty,
            order_id=order_id if self._is_mbo else None,
            source=self.name,
        )

    def _make_trade(
        self,
        book: _ShadowBook,
        rng: random.Random,
        symbol: str,
        seq: int,
        t: int,
        max_qty: int,
    ) -> MarketEvent | None:
        aggressor = Side.BUY if rng.random() < 0.5 else Side.SELL
        resting = aggressor.opposite
        best = book.available_at_best(resting)
        if best is None:
            return None
        price, available = best
        requested = min(available, rng.randint(1, max_qty))
        order_id, qty = book.trade(aggressor, price, requested)
        if qty <= 0:
            return None
        flag = EventFlag.AGGRESSOR_BUY if aggressor is Side.BUY else EventFlag.AGGRESSOR_SELL
        return MarketEvent(
            sequence_id=seq,
            exchange_timestamp_ns=t,
            symbol=symbol,
            event_type=EventType.TRADE,
            side=aggressor,
            price_ticks=price,
            quantity_base=qty,
            order_id=order_id if self._is_mbo else None,
            trade_id=seq,
            flags=flag,
            source=self.name,
        )

    def _pick_add_price(self, book: _ShadowBook, side: Side, rng: random.Random) -> int | None:
        """Choose a price near the touch without ever crossing the book."""
        best_bid = book.bids.best_price()
        best_ask = book.asks.best_price()
        offset = rng.choice(_PRICE_OFFSETS)
        if side is Side.BUY:
            if best_bid is None:
                base = (best_ask - 1) if best_ask is not None else 0
            else:
                base = best_bid
            price = base - offset
            if best_ask is not None and price >= best_ask:
                price = best_ask - 1
        else:
            if best_ask is None:
                base = (best_bid + 1) if best_bid is not None else 1
            else:
                base = best_ask
            price = base + offset
            if best_bid is not None and price <= best_bid:
                price = best_bid + 1
        return max(price, 1)
