"""One side of an aggregated (MBP) book.

Prices are held in a dictionary plus a sorted price list, so best-price access
is O(1) and level insert/erase is O(log n) lookup + O(n) list shift. The Python
side is the *reference* implementation (ADR-002); the production core is the C++
book, which uses a dense tick-indexed array.

The list is kept sorted **ascending** by price for both sides. For bids the best
price is the last element; for asks it is the first.
"""

from __future__ import annotations

from bisect import bisect_left, insort

from ..domain.book import PriceLevel
from ..domain.exceptions import BookIntegrityError


class SideLevels:
    """Aggregated quantities for one side of the book."""

    __slots__ = ("_is_bid", "_levels", "_max_levels", "_prices")

    def __init__(self, is_bid: bool, max_levels: int = 500) -> None:
        self._is_bid = is_bid
        self._levels: dict[int, int] = {}
        self._prices: list[int] = []
        self._max_levels = max_levels

    # ------------------------------------------------------------- mutation

    def add(self, price_ticks: int, quantity_base: int) -> None:
        if quantity_base == 0:
            return
        current = self._levels.get(price_ticks)
        if current is None:
            if len(self._prices) >= self._max_levels:
                raise BookIntegrityError(
                    f"level limit {self._max_levels} exceeded on "
                    f"{'bid' if self._is_bid else 'ask'} side"
                )
            self._levels[price_ticks] = quantity_base
            insort(self._prices, price_ticks)
        else:
            self._levels[price_ticks] = current + quantity_base

    def remove(self, price_ticks: int, quantity_base: int) -> int:
        """Remove quantity at a price; returns the quantity actually removed.

        Raises if the level does not exist (unknown cancel) or would go negative.
        """
        if quantity_base == 0:
            return 0
        current = self._levels.get(price_ticks)
        if current is None:
            raise BookIntegrityError(
                f"remove at empty price {price_ticks} on {'bid' if self._is_bid else 'ask'} side"
            )
        if current < quantity_base:
            raise BookIntegrityError(
                f"negative depth at price {price_ticks}: have {current}, remove {quantity_base}"
            )
        new_qty = current - quantity_base
        if new_qty == 0:
            del self._levels[price_ticks]
            idx = bisect_left(self._prices, price_ticks)
            if idx < len(self._prices) and self._prices[idx] == price_ticks:
                self._prices.pop(idx)
        else:
            self._levels[price_ticks] = new_qty
        return quantity_base

    def set_level(self, price_ticks: int, quantity_base: int) -> None:
        """Absolute set (used by SNAPSHOT)."""
        if quantity_base <= 0:
            if price_ticks in self._levels:
                self.remove(price_ticks, self._levels[price_ticks])
            return
        if price_ticks in self._levels:
            self._levels[price_ticks] = quantity_base
        else:
            self.add(price_ticks, quantity_base)

    def clear(self) -> None:
        self._levels.clear()
        self._prices.clear()

    # --------------------------------------------------------------- access

    @property
    def is_bid(self) -> bool:
        return self._is_bid

    def quantity_at(self, price_ticks: int) -> int:
        return self._levels.get(price_ticks, 0)

    def best_price(self) -> int | None:
        if not self._prices:
            return None
        return self._prices[-1] if self._is_bid else self._prices[0]

    def best_quantity(self) -> int:
        best = self.best_price()
        return 0 if best is None else self._levels[best]

    def worst_price(self) -> int | None:
        if not self._prices:
            return None
        return self._prices[0] if self._is_bid else self._prices[-1]

    def n_levels(self) -> int:
        return len(self._prices)

    def total_quantity(self) -> int:
        return sum(self._levels.values())

    def levels_best_first(self, depth: int | None = None) -> tuple[PriceLevel, ...]:
        """Levels ordered best-first, truncated to `depth`."""
        prices = self._prices[::-1] if self._is_bid else self._prices
        if depth is not None:
            prices = prices[:depth]
        return tuple(PriceLevel(price_ticks=p, quantity_base=self._levels[p]) for p in prices)

    def prices_best_first(self) -> tuple[int, ...]:
        prices = self._prices[::-1] if self._is_bid else self._prices
        return tuple(prices)
