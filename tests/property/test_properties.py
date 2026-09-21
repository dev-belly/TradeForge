"""Property-based tests.

These check invariants over generated inputs rather than hand-picked examples.
They are the tests most likely to find a bug the author did not imagine, which
is exactly the point.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tradeforge.domain.book import BookSnapshot
from tradeforge.domain.enums import DataType, EventType, Side
from tradeforge.domain.events import MarketEvent
from tradeforge.domain.exceptions import BookIntegrityError
from tradeforge.domain.instrument import InstrumentSpec
from tradeforge.orderbook import BookSettings, create_book
from tradeforge.research.bootstrap import block_bootstrap_ci

TICKS = st.integers(min_value=5_000, max_value=20_000)
QUANTITIES = st.integers(min_value=1, max_value=5_000)
SIDES = st.sampled_from([Side.BUY, Side.SELL])


@given(tick_size=st.sampled_from(["0.01", "0.001", "0.05", "1", "0.25"]))
@settings(max_examples=60, suppress_health_check=[HealthCheck.too_slow])
def test_price_tick_round_trip_is_exact(tick_size: str):
    """Converting ticks to a decimal price and back must be lossless."""
    spec = InstrumentSpec(
        symbol="P",
        tick_size=Decimal(tick_size),
        lot_size=1,
        currency="USD",
        price_band_lower_ticks=1,
        price_band_upper_ticks=1_000_000,
    )
    for ticks in (1, 7, 999, 123_456):
        assert spec.price_to_ticks(spec.ticks_to_decimal(ticks)) == ticks


@given(
    tick_size=st.sampled_from(["0.01", "0.05"]),
    quantity=st.integers(min_value=0, max_value=100_000),
    lot=st.integers(min_value=1, max_value=100),
)
@settings(max_examples=80)
def test_lot_rounding_never_exceeds_the_input(tick_size: str, quantity: int, lot: int):
    spec = InstrumentSpec(
        symbol="P",
        tick_size=Decimal(tick_size),
        lot_size=lot,
        currency="USD",
        price_band_lower_ticks=1,
        price_band_upper_ticks=1_000_000,
    )
    rounded = spec.round_to_lot(quantity)
    assert rounded <= quantity
    assert rounded % lot == 0


@given(adds=st.lists(st.tuples(SIDES, TICKS, QUANTITIES), min_size=1, max_size=40))
@settings(max_examples=40, deadline=None)
def test_a_crossed_book_is_either_prevented_or_reported(adds):
    """The book never *ends up* crossed without saying so.

    Two acceptable outcomes when an ADD would cross the market:

      * the event is refused with `BookIntegrityError` (STRICT mode, the
        default), or
      * the crossing is tolerated and reported through the violation hook.

    What is never acceptable is a silently crossed book. A locked market
    (bid == ask) is a separate, milder case: tolerated and warned about, because
    it genuinely occurs on real venues.
    """
    reported: list[str] = []
    book = create_book(
        symbol="P",
        settings=BookSettings.from_dict({}),
        data_type=DataType.L2_MBP,
        on_violation=lambda violation, message, event: reported.append(violation.value),
    )
    for index, (side, price, quantity) in enumerate(adds):
        event = MarketEvent(
            sequence_id=index,
            exchange_timestamp_ns=index,
            symbol="P",
            event_type=EventType.ADD,
            side=side,
            price_ticks=price,
            quantity_base=quantity,
            source="property",
        )
        try:
            book.apply(event)
        except BookIntegrityError:
            return  # refused, which is the strict-mode contract

    best_bid = book.best_bid_ticks
    best_ask = book.best_ask_ticks
    if best_bid is None or best_ask is None:
        return
    assert best_bid <= best_ask, f"book silently crossed: {best_bid} > {best_ask}"
    if best_bid == best_ask:
        assert "locked_market" in reported, "a locked market must be reported"


@given(price=TICKS, quantity=QUANTITIES)
@settings(max_examples=30, deadline=None)
def test_a_locked_market_is_reported_rather_than_silently_accepted(price, quantity):
    """Locked is tolerated but must be visible to the caller."""
    warnings: list[str] = []
    book = create_book(
        symbol="P",
        settings=BookSettings.from_dict({}),
        data_type=DataType.L2_MBP,
        on_violation=lambda violation, message, event: warnings.append(violation.value),
    )
    for index, side in enumerate((Side.BUY, Side.SELL)):
        book.apply(
            MarketEvent(
                sequence_id=index,
                exchange_timestamp_ns=index,
                symbol="P",
                event_type=EventType.ADD,
                side=side,
                price_ticks=price,
                quantity_base=quantity,
                source="property",
            )
        )
    assert book.best_bid_ticks == book.best_ask_ticks
    assert "locked_market" in warnings


@given(
    side=SIDES,
    price=TICKS,
    quantities=st.lists(st.integers(min_value=1, max_value=1_000), min_size=2, max_size=20),
)
@settings(max_examples=60, deadline=None)
def test_depth_is_the_sum_of_added_quantities(side, price, quantities):
    book = create_book(symbol="P", settings=BookSettings.from_dict({}), data_type=DataType.L2_MBP)
    for index, quantity in enumerate(quantities):
        book.apply(
            MarketEvent(
                sequence_id=index,
                exchange_timestamp_ns=index,
                symbol="P",
                event_type=EventType.ADD,
                side=side,
                price_ticks=price,
                quantity_base=quantity,
                source="property",
            )
        )
    assert book.level_size_base(side, price) == sum(quantities)


@given(
    side=SIDES,
    price=TICKS,
    add=st.integers(min_value=100, max_value=5_000),
    cancel=st.integers(min_value=1, max_value=100),
)
@settings(max_examples=60, deadline=None)
def test_cancelling_within_depth_never_raises(side, price, add, cancel):
    book = create_book(symbol="P", settings=BookSettings.from_dict({}), data_type=DataType.L2_MBP)
    book.apply(
        MarketEvent(
            sequence_id=0,
            exchange_timestamp_ns=0,
            symbol="P",
            event_type=EventType.ADD,
            side=side,
            price_ticks=price,
            quantity_base=add,
            source="property",
        )
    )
    book.apply(
        MarketEvent(
            sequence_id=1,
            exchange_timestamp_ns=1,
            symbol="P",
            event_type=EventType.CANCEL,
            side=side,
            price_ticks=price,
            quantity_base=cancel,
            source="property",
        )
    )
    assert book.level_size_base(side, price) == add - cancel


@given(
    values=st.lists(
        st.floats(min_value=-100.0, max_value=100.0, allow_nan=False, allow_infinity=False),
        min_size=3,
        max_size=200,
    )
)
@settings(max_examples=40, deadline=None)
def test_bootstrap_interval_contains_the_estimate(values):
    ci = block_bootstrap_ci(values, block_length=5, n_resamples=200, seed=3)
    assert ci.lower <= ci.estimate <= ci.upper


@given(
    values=st.lists(
        st.floats(min_value=-10.0, max_value=10.0, allow_nan=False, allow_infinity=False),
        min_size=5,
        max_size=100,
    ),
    seed=st.integers(min_value=0, max_value=10_000),
)
@settings(max_examples=30, deadline=None)
def test_bootstrap_is_reproducible_for_a_seed(values, seed):
    first = block_bootstrap_ci(values, block_length=4, n_resamples=150, seed=seed)
    second = block_bootstrap_ci(values, block_length=4, n_resamples=150, seed=seed)
    assert (first.lower, first.upper) == (second.lower, second.upper)


@given(
    bid_qty=st.integers(min_value=1, max_value=10_000),
    ask_qty=st.integers(min_value=1, max_value=10_000),
)
@settings(max_examples=80)
def test_microprice_stays_inside_the_spread(bid_qty: int, ask_qty: int):
    """The microprice is a convex combination, so it cannot leave the quotes."""
    from tradeforge.orderbook import microprice_ticks

    snapshot = BookSnapshot(
        timestamp_ns=0,
        sequence_id=0,
        symbol="P",
        bids=(_level(9_999, bid_qty),),
        asks=(_level(10_001, ask_qty),),
    )
    value = microprice_ticks(snapshot)
    assert 9_999.0 <= value <= 10_001.0


def _level(price: int, quantity: int):
    from tradeforge.domain.book import PriceLevel

    return PriceLevel(price_ticks=price, quantity_base=quantity)


@pytest.mark.parametrize("quantity", [1, 2, 3, 5, 8, 13])
def test_fibonacci_quantities_are_all_accepted_by_the_instrument(quantity: int):
    spec = InstrumentSpec(
        symbol="P",
        tick_size=Decimal("0.01"),
        lot_size=1,
        currency="USD",
        price_band_lower_ticks=1,
        price_band_upper_ticks=1_000_000,
    )
    spec.validate_quantity(quantity)
    assert spec.round_to_lot(quantity) == quantity
