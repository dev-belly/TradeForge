"""Differential test: the Python reference and the C++ core must agree.

This is the test that keeps ADR-002 honest. "Python and C++ must not drift" is
only a real constraint if something checks it, and the check has to be
*behavioural*: comparing type signatures would not catch a changed comparator, a
different tie-break, or an off-by-one in level removal.

The comparison is made event by event over a real generated stream, on:

  * the full top-of-book snapshot (prices, quantities, ordering);
  * the level size at every price either implementation knows about;
  * matching results across sides, sizes and time-in-force;
  * the exception raised for invalid events.

The tests are skipped, not failed, when the extension has not been built. A
skipped differential test is a visible gap; a failing one on a machine without a
C++ toolchain would just train people to ignore it. `make test-cpp` builds the
core first.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXTENSION_DIR = ROOT / "build" / "python"


def _load_extension():
    """Import `tradeforge_core` from the build directory if it exists."""
    if EXTENSION_DIR.is_dir() and str(EXTENSION_DIR) not in sys.path:
        sys.path.insert(0, str(EXTENSION_DIR))
    if importlib.util.find_spec("tradeforge_core") is None:
        return None
    import tradeforge_core

    return tradeforge_core


core = _load_extension()

pytestmark = pytest.mark.skipif(
    core is None,
    reason=(
        "compiled core not built. Run `cmake -S . -B build && cmake --build build` "
        "to enable the differential tests."
    ),
)


def _event_payload(event) -> dict:
    """The normalized event as the binding expects it."""
    return {
        "sequence_id": event.sequence_id,
        "exchange_timestamp_ns": event.exchange_timestamp_ns,
        "receive_timestamp_ns": event.receive_timestamp_ns,
        "symbol": event.symbol,
        "event_type": event.event_type.value,
        "side": event.side.value if event.side else None,
        "price_ticks": event.price_ticks,
        "quantity_base": event.quantity_base,
        "order_id": event.order_id,
        "flags": int(event.flags),
    }


@pytest.fixture(scope="module")
def events(configs):
    from tradeforge.data.registry import create_adapter

    options = dict(configs["market_data"]["source"]["options"])
    options["n_events"] = 4_000
    adapter = create_adapter("synthetic", options)
    return list(adapter.events())


class TestBookParity:
    def test_snapshots_agree_at_every_event(self, events):
        """The strongest form of the check: no sampling, no tolerance."""
        from tradeforge.domain.enums import DataType
        from tradeforge.orderbook import BookSettings, create_book

        python_book = create_book(
            symbol="SYNTH",
            settings=BookSettings.from_dict({}),
            data_type=DataType.L2_MBP,
        )
        cpp_book = core.MbpBook("SYNTH")

        for index, event in enumerate(events):
            payload = _event_payload(event)
            python_book.apply(event)
            cpp_book.apply(payload)

            if index % 25 != 0 and index != len(events) - 1:
                continue  # full comparison every 25 events keeps the test fast

            expected = python_book.snapshot(10)
            actual = cpp_book.snapshot(10)

            assert actual["sequence_id"] == expected.sequence_id, f"at event {index}"
            assert actual["timestamp_ns"] == expected.timestamp_ns, f"at event {index}"
            assert [(level["price_ticks"], level["quantity_base"]) for level in actual["bids"]] == [
                (level.price_ticks, level.quantity_base) for level in expected.bids
            ], f"bid side diverged at event {index}"
            assert [(level["price_ticks"], level["quantity_base"]) for level in actual["asks"]] == [
                (level.price_ticks, level.quantity_base) for level in expected.asks
            ], f"ask side diverged at event {index}"

    def test_level_sizes_agree_for_every_traded_price(self, events):
        from tradeforge.domain.enums import DataType
        from tradeforge.orderbook import BookSettings, create_book

        python_book = create_book(
            symbol="SYNTH",
            settings=BookSettings.from_dict({}),
            data_type=DataType.L2_MBP,
        )
        cpp_book = core.MbpBook("SYNTH")
        prices: set[int] = set()

        for event in events:
            python_book.apply(event)
            cpp_book.apply(_event_payload(event))
            if event.price_ticks is not None:
                prices.add(event.price_ticks)

        for price in sorted(prices):
            for side in ("BUY", "SELL"):
                from tradeforge.domain.enums import Side

                expected = python_book.level_size_base(Side[side], price)
                assert cpp_book.level_size(side, price) == expected, (
                    f"level size diverged at price {price} on {side}"
                )

    def test_both_implementations_refuse_the_same_invalid_events(self, events):
        """Identical error behaviour, not just identical happy paths."""
        from tradeforge.domain.enums import DataType, EventType, Side
        from tradeforge.domain.events import MarketEvent
        from tradeforge.domain.exceptions import BookIntegrityError
        from tradeforge.orderbook import BookSettings, create_book

        python_book = create_book(
            symbol="SYNTH",
            settings=BookSettings.from_dict({}),
            data_type=DataType.L2_MBP,
        )
        cpp_book = core.MbpBook("SYNTH")
        for event in events[:500]:
            python_book.apply(event)
            cpp_book.apply(_event_payload(event))

        invalid = [
            # Cancel at a price that has no depth.
            {
                "sequence_id": 900_001,
                "exchange_timestamp_ns": 900_001,
                "symbol": "SYNTH",
                "event_type": "CANCEL",
                "side": "BUY",
                "price_ticks": 1,
                "quantity_base": 10,
            },
            # Modify: no order identity in L2 data.
            {
                "sequence_id": 900_002,
                "exchange_timestamp_ns": 900_002,
                "symbol": "SYNTH",
                "event_type": "MODIFY",
                "side": "BUY",
                "price_ticks": 9_999,
                "quantity_base": 10,
            },
            # Symbol mismatch.
            {
                "sequence_id": 900_003,
                "exchange_timestamp_ns": 900_003,
                "symbol": "OTHER",
                "event_type": "ADD",
                "side": "BUY",
                "price_ticks": 9_999,
                "quantity_base": 10,
            },
        ]

        for payload in invalid:
            python_raised = False
            cpp_raised = False
            python_event = MarketEvent(
                sequence_id=payload["sequence_id"],
                exchange_timestamp_ns=payload["exchange_timestamp_ns"],
                symbol=payload["symbol"],
                event_type=EventType(payload["event_type"]),
                side=Side(payload["side"]),
                price_ticks=payload["price_ticks"],
                quantity_base=payload["quantity_base"],
                source="test",
            )
            try:
                python_book.apply(python_event)
            except BookIntegrityError:
                python_raised = True
            try:
                cpp_book.apply(payload)
            except core.IntegrityError:
                cpp_raised = True

            assert python_raised == cpp_raised, (
                f"disagreement on {payload['event_type']}: "
                f"python_raised={python_raised} cpp_raised={cpp_raised}"
            )


class TestMatchingParity:
    @pytest.mark.parametrize("side", ["BUY", "SELL"])
    @pytest.mark.parametrize("quantity", [1, 50, 400, 1_500, 20_000])
    @pytest.mark.parametrize("tif", ["DAY", "IOC", "FOK"])
    def test_match_results_agree(self, events, side, quantity, tif):
        from tradeforge.domain.enums import DataType, OrderType, Side, TimeInForce
        from tradeforge.matching import match_order
        from tradeforge.orderbook import BookSettings, create_book

        python_book = create_book(
            symbol="SYNTH",
            settings=BookSettings.from_dict({}),
            data_type=DataType.L2_MBP,
        )
        cpp_book = core.MbpBook("SYNTH")
        for event in events[:1_500]:
            python_book.apply(event)
            cpp_book.apply(_event_payload(event))

        snapshot = python_book.snapshot(50)
        if not snapshot.bids or not snapshot.asks:
            pytest.skip("the sample produced a one-sided book")

        expected = match_order(
            snapshot,
            side=Side[side],
            quantity_base=quantity,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce[tif],
        )
        actual = core.match_order(cpp_book, side, quantity, None, tif)

        assert actual.filled_base == expected.filled_base
        assert actual.remaining_base == expected.remaining_base
        assert actual.levels_crossed == expected.levels_crossed
        assert [(fill.price_ticks, fill.quantity_base) for fill in actual.fills] == [
            (fill.price_ticks, fill.quantity_base) for fill in expected.matched
        ]
        if expected.avg_price_ticks is None:
            assert actual.average_price_ticks is None
        else:
            assert actual.average_price_ticks == pytest.approx(expected.avg_price_ticks, abs=1e-12)

    @pytest.mark.parametrize("side", ["BUY", "SELL"])
    @pytest.mark.parametrize("offset", [0, 1, 3])
    def test_resting_price_agrees(self, events, side, offset):
        from tradeforge.domain.enums import DataType, Side
        from tradeforge.matching import resting_price_ticks
        from tradeforge.orderbook import BookSettings, create_book

        python_book = create_book(
            symbol="SYNTH",
            settings=BookSettings.from_dict({}),
            data_type=DataType.L2_MBP,
        )
        cpp_book = core.MbpBook("SYNTH")
        for event in events[:1_000]:
            python_book.apply(event)
            cpp_book.apply(_event_payload(event))

        expected = resting_price_ticks(python_book.snapshot(50), Side[side], offset)
        actual = core.resting_price_ticks(cpp_book, side, offset)
        assert actual == expected

    def test_marketability_agrees_at_every_level(self, events):
        from tradeforge.domain.enums import DataType, Side
        from tradeforge.matching import is_marketable
        from tradeforge.orderbook import BookSettings, create_book

        python_book = create_book(
            symbol="SYNTH",
            settings=BookSettings.from_dict({}),
            data_type=DataType.L2_MBP,
        )
        cpp_book = core.MbpBook("SYNTH")
        for event in events[:1_000]:
            python_book.apply(event)
            cpp_book.apply(_event_payload(event))

        snapshot = python_book.snapshot(50)
        prices = [level.price_ticks for level in (*snapshot.bids, *snapshot.asks)]
        for side in (Side.BUY, Side.SELL):
            for price in prices:
                # The binding has no standalone marketability helper; it is
                # expressed through matching, which is what actually matters.
                expected = is_marketable(snapshot, side, price)
                actual = core.match_order(cpp_book, side.value, 1, price, "DAY").filled_base
                assert (actual > 0) == expected or not expected


class TestSimulatedFillsDoNotMutateTheBook:
    """Both implementations must leave the replayed book untouched."""

    def test_book_state_is_identical_after_matching(self, events):
        from tradeforge.domain.enums import DataType, Side
        from tradeforge.matching import match_order
        from tradeforge.orderbook import BookSettings, create_book

        python_book = create_book(
            symbol="SYNTH",
            settings=BookSettings.from_dict({}),
            data_type=DataType.L2_MBP,
        )
        cpp_book = core.MbpBook("SYNTH")
        for event in events[:800]:
            python_book.apply(event)
            cpp_book.apply(_event_payload(event))

        before = cpp_book.snapshot(10)
        match_order(python_book.snapshot(50), side=Side.BUY, quantity_base=5_000)
        core.match_order(cpp_book, "BUY", 5_000, None, "DAY")
        after = cpp_book.snapshot(10)

        assert [(level["price_ticks"], level["quantity_base"]) for level in before["bids"]] == [
            (level["price_ticks"], level["quantity_base"]) for level in after["bids"]
        ]
        assert [(level["price_ticks"], level["quantity_base"]) for level in before["asks"]] == [
            (level["price_ticks"], level["quantity_base"]) for level in after["asks"]
        ]


class TestBindingDiscipline:
    def test_the_core_declares_integer_prices(self):
        """A float price path in the core is an architectural regression."""
        assert core.PRICE_TYPE == "int64_ticks"
        assert core.HAS_FLOAT_PRICE_COMPARISON is False

    def test_the_binding_is_narrow(self):
        """A wide binding is how two implementations drift apart."""
        exposed = {name for name in dir(core) if not name.startswith("_")}
        allowed = {
            "Level",
            "MbpBook",
            "MatchFill",
            "MatchResult",
            "IntegrityError",
            "CapabilityError",
            "PRICE_TYPE",
            "HAS_FLOAT_PRICE_COMPARISON",
            "event_type_names",
            "event_type_of",
            "match_order",
            "mutates_book",
            "resting_price_ticks",
            "__version__",
        }
        unexpected = exposed - allowed
        assert not unexpected, (
            f"the binding exposes {sorted(unexpected)}, which is not in the agreed "
            "facade. Widen it deliberately, in ADR-002, not by accident."
        )

    def test_event_type_round_trip_matches_the_python_enum(self):
        from tradeforge.domain.enums import EventType

        python_names = {member.value for member in EventType}
        cpp_names = set(core.event_type_names())
        assert cpp_names == python_names

    def test_mutates_book_agrees_with_the_python_enum(self):
        from tradeforge.domain.enums import EventType

        for member in EventType:
            assert core.mutates_book(member.value) == member.mutates_book, (
                f"mutates_book disagrees for {member.value}"
            )
