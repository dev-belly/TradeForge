"""Order book value objects and the observable market state.

`BookSnapshot` and `MarketState` are immutable value objects handed to policies.
A policy that receives them cannot mutate the book, because there is nothing
behind them to mutate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .enums import Side


@dataclass(frozen=True, slots=True)
class PriceLevel:
    """One aggregated price level.

    `order_count` is only meaningful for MBO/L3 books; for MBP it stays 0
    because the source does not reveal how many orders make up the level.
    """

    price_ticks: int
    quantity_base: int
    order_count: int = 0


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    """Immutable view of the reconstructed book at one instant.

    `bids` are sorted best (highest price) first, `asks` best (lowest) first.
    """

    timestamp_ns: int
    sequence_id: int
    symbol: str
    bids: tuple[PriceLevel, ...] = ()
    asks: tuple[PriceLevel, ...] = ()

    # ---------------------------------------------------------------- touch

    @property
    def best_bid(self) -> PriceLevel | None:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> PriceLevel | None:
        return self.asks[0] if self.asks else None

    @property
    def best_bid_ticks(self) -> int | None:
        return self.bids[0].price_ticks if self.bids else None

    @property
    def best_ask_ticks(self) -> int | None:
        return self.asks[0].price_ticks if self.asks else None

    @property
    def best_bid_qty_base(self) -> int:
        return self.bids[0].quantity_base if self.bids else 0

    @property
    def best_ask_qty_base(self) -> int:
        return self.asks[0].quantity_base if self.asks else 0

    @property
    def mid_ticks(self) -> float | None:
        """Mid in ticks; float by construction (average of two integers)."""
        if not self.bids or not self.asks:
            return None
        return (self.bids[0].price_ticks + self.asks[0].price_ticks) / 2.0

    @property
    def spread_ticks(self) -> int | None:
        if not self.bids or not self.asks:
            return None
        return self.asks[0].price_ticks - self.bids[0].price_ticks

    @property
    def is_crossed(self) -> bool:
        if not self.bids or not self.asks:
            return False
        return self.bids[0].price_ticks >= self.asks[0].price_ticks

    @property
    def is_locked(self) -> bool:
        if not self.bids or not self.asks:
            return False
        return self.bids[0].price_ticks == self.asks[0].price_ticks

    # ---------------------------------------------------------------- depth

    def depth_base(self, side: Side, levels: int | None = None) -> int:
        book = self.bids if side is Side.BUY else self.asks
        if levels is not None:
            book = book[:levels]
        return sum(level.quantity_base for level in book)

    def levels(self, side: Side, levels: int | None = None) -> tuple[PriceLevel, ...]:
        book = self.bids if side is Side.BUY else self.asks
        return book if levels is None else book[:levels]

    def touch_ticks(self, side: Side) -> int | None:
        """The price an aggressive order of `side` would cross into."""
        return self.best_ask_ticks if side is Side.BUY else self.best_bid_ticks


@dataclass(frozen=True, slots=True)
class MarketState:
    """Everything a policy is allowed to observe at time `timestamp_ns`.

    Contains only present and past-derived quantities. There is deliberately no
    field that can carry a future event, a full-day statistic, or a handle to
    the event stream.
    """

    symbol: str
    timestamp_ns: int
    sequence_id: int
    best_bid_ticks: int | None
    best_ask_ticks: int | None
    best_bid_qty_base: int
    best_ask_qty_base: int
    bid_levels: tuple[PriceLevel, ...]
    ask_levels: tuple[PriceLevel, ...]
    last_trade_ticks: int | None
    last_trade_qty_base: int
    aggressor_side_known: bool
    # Cumulative traded volume since the start of the stream. POV differences
    # consecutive readings to get interval volume, so this must never be a
    # rolling window (see MicrostructureEngine._total_market_volume).
    market_volume_base: int = 0
    # Rolling-window traded volume (a different quantity from the above).
    recent_volume_base: int = 0
    recent_trade_count: int = 0
    recent_buy_volume_base: int = 0
    recent_sell_volume_base: int = 0
    book_updates: int = 0
    halted: bool = False

    @property
    def mid_ticks(self) -> float | None:
        if self.best_bid_ticks is None or self.best_ask_ticks is None:
            return None
        return (self.best_bid_ticks + self.best_ask_ticks) / 2.0

    @property
    def spread_ticks(self) -> int | None:
        if self.best_bid_ticks is None or self.best_ask_ticks is None:
            return None
        return self.best_ask_ticks - self.best_bid_ticks

    @property
    def has_two_sided_book(self) -> bool:
        return self.best_bid_ticks is not None and self.best_ask_ticks is not None

    def touch_ticks(self, side: Side) -> int | None:
        """The price our aggressive order of `side` would cross into."""
        return self.best_ask_ticks if side is Side.BUY else self.best_bid_ticks


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """A single observed trade, kept in rolling windows for features."""

    timestamp_ns: int
    price_ticks: int
    quantity_base: int
    aggressor_side: Side | None


@dataclass(frozen=True, slots=True)
class RollingWindowConfig:
    """Configuration for causal rolling windows (past only)."""

    window_ns: int = 60_000_000_000
    max_events: int = 10_000


@dataclass(slots=True)
class _RollingState:
    """Internal mutable accumulator; only ever read through MarketState."""

    trades: list[TradeRecord] = field(default_factory=list)
    volume_base: int = 0
    buy_volume_base: int = 0
    sell_volume_base: int = 0
