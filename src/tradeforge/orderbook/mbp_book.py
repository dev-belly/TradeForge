"""Market-by-price (MBP / L2) order book reconstruction.

Reference implementation in Python (ADR-002). Applies observed events to
aggregated price levels. It never invents order identity and never claims
anything about queue position.

Documented reconstruction choices:
  * ADD    : +quantity at price
  * CANCEL : -quantity at price
  * TRADE  : consume quantity from the *resting* side at the trade price.
             A single print cannot reveal a multi-level sweep, so we consume
             only at the print price.
  * SNAPSHOT: absolute set for one level; the adapter emits CLEAR first.
  * MODIFY : not interpretable without order identity -> rejected.
"""

from __future__ import annotations

from collections.abc import Callable

from ..domain.book import BookSnapshot, PriceLevel
from ..domain.enums import EventType, Side, ValidationMode
from ..domain.events import MarketEvent
from ..domain.exceptions import BookIntegrityError
from ..domain.instrument import InstrumentSpec
from .config import BookSettings
from .invariants import InvariantViolation, check_book_shape, should_raise
from .levels import SideLevels

ViolationHook = Callable[[InvariantViolation, str, MarketEvent], None]


class MbpBook:
    """Aggregated L2 book. Mutable internal state, immutable snapshots out."""

    def __init__(
        self,
        symbol: str,
        settings: BookSettings,
        spec: InstrumentSpec | None = None,
        on_violation: ViolationHook | None = None,
    ) -> None:
        self._symbol = symbol
        self._settings = settings
        self._spec = spec
        self._on_violation = on_violation
        cap = settings.max_levels_per_side
        self._bids = SideLevels(is_bid=True, max_levels=cap)
        self._asks = SideLevels(is_bid=False, max_levels=cap)
        self._last_sequence_id = -1
        self._last_timestamp_ns = 0
        self._update_count = 0
        self._modes: dict[InvariantViolation, ValidationMode] = {
            InvariantViolation.CROSSED_BOOK: settings.on_crossed_book,
            InvariantViolation.LOCKED_MARKET: settings.on_locked_market,
            InvariantViolation.NEGATIVE_DEPTH: settings.on_negative_depth,
            InvariantViolation.UNKNOWN_ORDER_ID: settings.on_unknown_order_id,
            InvariantViolation.TRADE_EXCEEDS_DEPTH: settings.on_negative_depth,
            InvariantViolation.PRICE_OUT_OF_BAND: settings.on_negative_depth,
        }

    # ------------------------------------------------------------ properties

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def last_sequence_id(self) -> int:
        return self._last_sequence_id

    @property
    def last_timestamp_ns(self) -> int:
        return self._last_timestamp_ns

    @property
    def update_count(self) -> int:
        return self._update_count

    @property
    def best_bid_ticks(self) -> int | None:
        return self._bids.best_price()

    @property
    def best_ask_ticks(self) -> int | None:
        return self._asks.best_price()

    # ---------------------------------------------------------------- apply

    def apply(self, event: MarketEvent) -> None:
        if event.symbol != self._symbol:
            raise BookIntegrityError(f"symbol mismatch: book={self._symbol} event={event.symbol}")
        if not event.event_type.mutates_book:
            self._observe(event)
            return

        if event.event_type is not EventType.CLEAR:
            price, quantity = self._require_price_and_quantity(event)
            if self._spec is not None:
                self._check_band(event, price)
        else:
            price = quantity = 0

        self._bump(event)
        if event.event_type is EventType.ADD:
            self._side_for(event).add(price, quantity)
        elif event.event_type is EventType.CANCEL:
            self._side_for(event).remove(price, quantity)
        elif event.event_type is EventType.SNAPSHOT:
            self._side_for(event).set_level(price, quantity)
        elif event.event_type is EventType.TRADE:
            self._apply_trade(event, price, quantity)
        elif event.event_type is EventType.CLEAR:
            self._bids.clear()
            self._asks.clear()
        elif event.event_type in (EventType.MODIFY, EventType.REPLACE):
            raise BookIntegrityError(
                f"{event.event_type.value} requires order identity; MBP book cannot "
                "interpret it. Emit CANCEL + ADD instead."
            )
        self._check_invariants(event)

    # ------------------------------------------------------------- snapshot

    def snapshot(self, depth: int | None = None) -> BookSnapshot:
        levels = depth if depth is not None else self._settings.snapshot_depth
        return BookSnapshot(
            timestamp_ns=self._last_timestamp_ns,
            sequence_id=self._last_sequence_id,
            symbol=self._symbol,
            bids=self._bids.levels_best_first(levels),
            asks=self._asks.levels_best_first(levels),
        )

    def clear(self) -> None:
        self._bids.clear()
        self._asks.clear()

    # -------------------------------------------------------------- internal

    def _apply_trade(self, event: MarketEvent, price: int, quantity: int) -> None:
        aggressor = event.aggressor_side() or event.side
        if aggressor is None:
            if self._settings.unknown_aggressor_policy == "strict":
                raise BookIntegrityError(
                    f"trade event seq={event.sequence_id} has no aggressor side; "
                    "MBP reconstruction cannot determine which side to consume"
                )
            return  # skip: record-only trade
        resting = aggressor.opposite
        levels = self._bids if resting is Side.BUY else self._asks
        available = levels.quantity_at(price)
        if available < quantity:
            message = (
                f"trade of {quantity} at {price} exceeds "
                f"reconstructed depth {available} on {resting.value} side"
            )
            self._handle(InvariantViolation.TRADE_EXCEEDS_DEPTH, message, event)
            levels.remove(price, available)
            return
        levels.remove(price, quantity)

    def _side_for(self, event: MarketEvent) -> SideLevels:
        if event.side is Side.BUY:
            return self._bids
        if event.side is Side.SELL:
            return self._asks
        raise BookIntegrityError(
            f"{event.event_type.value} event requires a side, got None (seq={event.sequence_id})"
        )

    def _bump(self, event: MarketEvent) -> None:
        self._last_sequence_id = event.sequence_id
        self._last_timestamp_ns = event.exchange_timestamp_ns
        self._update_count += 1

    def _observe(self, event: MarketEvent) -> None:
        self._last_sequence_id = event.sequence_id
        self._last_timestamp_ns = event.exchange_timestamp_ns

    def _require_price_and_quantity(self, event: MarketEvent) -> tuple[int, int]:
        """Validate and return `(price_ticks, quantity_base)` as non-optional ints.

        Returning the narrowed values rather than `None` is what lets the type
        checker see the invariant the runtime already enforces. The previous
        version raised on a missing price but returned nothing, so every later
        `event.price_ticks` was still typed `int | None` and mypy flagged two
        dozen arithmetic errors in code that could not actually fail.
        """
        price = event.price_ticks
        quantity = event.quantity_base
        if price is None or quantity is None:
            raise BookIntegrityError(
                f"{event.event_type.value} event missing price/quantity (seq={event.sequence_id})"
            )
        if quantity <= 0:
            raise BookIntegrityError(f"non-positive quantity {quantity} (seq={event.sequence_id})")
        return price, quantity

    def _check_band(self, event: MarketEvent, price: int) -> None:
        if self._spec is None:
            return
        message = ""
        try:
            self._spec.validate_price_ticks(price)
            return
        except BookIntegrityError as exc:
            message = str(exc)
        self._handle(InvariantViolation.PRICE_OUT_OF_BAND, message, event)

    def _check_invariants(self, event: MarketEvent) -> None:
        for violation, message in check_book_shape(
            self.best_bid_ticks,
            self.best_ask_ticks,
            on_crossed=self._settings.on_crossed_book,
            on_locked=self._settings.on_locked_market,
        ):
            self._handle(violation, message, event)

    def _handle(self, violation: InvariantViolation, message: str, event: MarketEvent) -> None:
        if should_raise(violation, self._modes):
            raise BookIntegrityError(f"{violation.value}: {message}")
        if self._on_violation is not None:
            self._on_violation(violation, message, event)

    def depth_base(self, side: Side, levels: int | None = None) -> int:
        book = self._bids if side is Side.BUY else self._asks
        if levels is None:
            return book.total_quantity()
        return sum(pl.quantity_base for pl in book.levels_best_first(levels))

    def level(self, side: Side, price_ticks: int) -> PriceLevel:
        book = self._bids if side is Side.BUY else self._asks
        return PriceLevel(price_ticks=price_ticks, quantity_base=book.quantity_at(price_ticks))

    def level_size_base(self, side: Side, price_ticks: int) -> int:
        """Displayed size at one price, read before an event mutates the level."""
        book = self._bids if side is Side.BUY else self._asks
        return book.quantity_at(price_ticks)
