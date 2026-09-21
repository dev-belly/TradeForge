"""Book reconstruction: MBP aggregation, MBO identity, metrics, invariants."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from tradeforge.domain.book import BookSnapshot
from tradeforge.domain.enums import DataType, EventType, Side
from tradeforge.domain.events import MarketEvent
from tradeforge.domain.exceptions import BookIntegrityError, DataCapabilityError
from tradeforge.orderbook import (
    create_book,
    depth_slope,
    microprice_ticks,
    mid_ticks,
    multi_level_imbalance,
    quoted_spread_ticks,
    relative_spread_bps,
    top_imbalance,
)


def _event(seq, event_type, side=None, price=None, quantity=None, **kwargs):
    return MarketEvent(
        sequence_id=seq,
        exchange_timestamp_ns=1_000 + seq,
        symbol="TEST",
        event_type=event_type,
        side=side,
        price_ticks=price,
        quantity_base=quantity,
        source="test",
        **kwargs,
    )


class TestMbpBook:
    def test_add_and_snapshot(self, seeded_book):
        snapshot = seeded_book.snapshot(10)
        assert snapshot.best_bid_ticks == 9_999
        assert snapshot.best_ask_ticks == 10_001
        assert snapshot.best_bid_qty_base == 500
        assert snapshot.spread_ticks == 2
        assert not snapshot.is_crossed

    def test_levels_are_ordered_best_first(self, seeded_book):
        snapshot = seeded_book.snapshot(10)
        # Bids descend from the best (highest) price, asks ascend from the best
        # (lowest). Both sides are "best first", which is what the metrics and
        # the matching engine both rely on.
        assert [level.price_ticks for level in snapshot.bids] == [9_999, 9_998, 9_997]
        assert [level.price_ticks for level in snapshot.asks] == [10_001, 10_002, 10_003]

    def test_add_accumulates_at_the_same_price(self, seeded_book):
        seeded_book.apply(_event(99, EventType.ADD, Side.BUY, 9_999, 250))
        assert seeded_book.snapshot(10).best_bid_qty_base == 750

    def test_cancel_reduces_and_removes_empty_levels(self, seeded_book):
        seeded_book.apply(_event(99, EventType.CANCEL, Side.BUY, 9_997, 300))
        prices = [level.price_ticks for level in seeded_book.snapshot(10).bids]
        assert 9_997 not in prices

    def test_cancel_at_an_empty_price_is_an_integrity_error(self, seeded_book):
        with pytest.raises(BookIntegrityError):
            seeded_book.apply(_event(99, EventType.CANCEL, Side.BUY, 9_000, 10))

    def test_cancel_larger_than_depth_is_an_integrity_error(self, seeded_book):
        with pytest.raises(BookIntegrityError):
            seeded_book.apply(_event(99, EventType.CANCEL, Side.BUY, 9_997, 301))

    def test_trade_consumes_the_resting_side(self, seeded_book):
        from tradeforge.domain.enums import EventFlag

        seeded_book.apply(
            _event(
                99,
                EventType.TRADE,
                Side.SELL,
                9_999,
                200,
                flags=EventFlag.AGGRESSOR_SELL,
            )
        )
        assert seeded_book.snapshot(10).best_bid_qty_base == 300

    def test_trade_without_aggressor_is_refused_in_strict_mode(self, seeded_book):
        with pytest.raises(BookIntegrityError) as excinfo:
            seeded_book.apply(_event(99, EventType.TRADE, None, 9_999, 100))
        assert "aggressor" in str(excinfo.value)

    def test_modify_is_rejected_without_order_identity(self, seeded_book):
        with pytest.raises(BookIntegrityError) as excinfo:
            seeded_book.apply(_event(99, EventType.MODIFY, Side.BUY, 9_999, 100))
        assert "order identity" in str(excinfo.value)

    def test_symbol_mismatch_is_rejected(self, seeded_book):
        wrong = MarketEvent(
            sequence_id=99,
            exchange_timestamp_ns=2_000,
            symbol="OTHER",
            event_type=EventType.ADD,
            side=Side.BUY,
            price_ticks=9_999,
            quantity_base=1,
        )
        with pytest.raises(BookIntegrityError):
            seeded_book.apply(wrong)

    def test_halt_does_not_mutate_the_book(self, seeded_book):
        before = seeded_book.snapshot(10)
        seeded_book.apply(_event(99, EventType.HALT))
        assert seeded_book.snapshot(10).bids == before.bids


class TestMboBook:
    def test_mbo_settings_with_l2_data_are_refused(self, mbo_settings):
        """The book mode and the declared data tier must agree."""
        with pytest.raises(DataCapabilityError):
            create_book(symbol="TEST", settings=mbo_settings, data_type=DataType.L2_MBP)

    def test_mbp_settings_with_l3_data_give_an_mbp_book(self, book_settings):
        """L3 data does not force an order-level book; the mode decides."""
        book = create_book(symbol="TEST", settings=book_settings, data_type=DataType.L3_MBO)
        assert book.__class__.__name__ == "MbpBook"

    def test_exact_queue_ahead_is_the_sum_of_earlier_orders(self, mbo_book):
        for index, quantity in enumerate((100, 200, 300)):
            mbo_book.apply(
                _event(index, EventType.ADD, Side.BUY, 9_999, quantity, order_id=index + 1)
            )
        assert mbo_book.queue_ahead_base(1) == 0
        assert mbo_book.queue_ahead_base(2) == 100
        assert mbo_book.queue_ahead_base(3) == 300

    def test_add_without_order_id_is_refused(self, mbo_book):
        with pytest.raises(BookIntegrityError):
            mbo_book.apply(_event(0, EventType.ADD, Side.BUY, 9_999, 100))

    def test_duplicate_order_id_is_refused(self, mbo_book):
        mbo_book.apply(_event(0, EventType.ADD, Side.BUY, 9_999, 100, order_id=7))
        with pytest.raises(BookIntegrityError):
            mbo_book.apply(_event(1, EventType.ADD, Side.BUY, 9_999, 100, order_id=7))

    def test_cancel_is_capped_by_the_named_order(self, mbo_book):
        mbo_book.apply(_event(0, EventType.ADD, Side.BUY, 9_999, 100, order_id=7))
        with pytest.raises(BookIntegrityError):
            mbo_book.apply(_event(1, EventType.CANCEL, Side.BUY, 9_999, 150, order_id=7))

    def test_snapshot_annotates_order_count(self, mbo_book):
        for index in range(3):
            mbo_book.apply(_event(index, EventType.ADD, Side.BUY, 9_999, 100, order_id=index + 1))
        level = mbo_book.snapshot(10).bids[0]
        assert level.quantity_base == 300
        assert level.order_count == 3

    def test_snapshot_event_is_meaningless_for_mbo(self, mbo_book):
        with pytest.raises(BookIntegrityError):
            mbo_book.apply(_event(0, EventType.SNAPSHOT, Side.BUY, 9_999, 100))


class TestMetrics:
    def test_mid_and_spread(self, seeded_book):
        snapshot = seeded_book.snapshot(10)
        assert mid_ticks(snapshot) == 10_000.0
        assert quoted_spread_ticks(snapshot) == 2

    def test_relative_spread_in_bps(self, seeded_book):
        # 2 ticks on a 10000-tick mid = 2 bps.
        assert relative_spread_bps(seeded_book.snapshot(10)) == pytest.approx(2.0)

    def test_microprice_leans_towards_the_thin_side(self, seeded_book):
        # bid 500, ask 400 -> more pressure on the ask -> microprice above mid.
        assert microprice_ticks(seeded_book.snapshot(10)) > 10_000.0

    def test_top_imbalance_sign(self, seeded_book):
        # bid 500 vs ask 400 -> positive imbalance.
        assert top_imbalance(seeded_book.snapshot(10)) > 0

    def test_multi_level_imbalance_uses_more_depth(self, seeded_book):
        value = multi_level_imbalance(seeded_book.snapshot(10), 3)
        assert value is not None
        assert -1.0 <= value <= 1.0

    def test_depth_slope_is_positive_when_depth_accumulates(self, seeded_book):
        assert depth_slope(seeded_book.snapshot(10), Side.BUY, 3) > 0

    def test_metrics_return_none_on_an_empty_book(self):
        empty = BookSnapshot(timestamp_ns=0, sequence_id=0, symbol="X")
        assert mid_ticks(empty) is None
        assert quoted_spread_ticks(empty) is None
        assert microprice_ticks(empty) is None
        assert top_imbalance(empty) is None
        assert relative_spread_bps(empty) is None


class TestSnapshotValueObject:
    def test_snapshot_is_immutable(self, seeded_book):
        snapshot = seeded_book.snapshot(10)
        with pytest.raises(FrozenInstanceError):
            snapshot.bids = ()

    def test_mutating_the_book_does_not_change_an_old_snapshot(self, seeded_book):
        before = seeded_book.snapshot(10)
        seeded_book.apply(_event(99, EventType.ADD, Side.BUY, 9_999, 1_000))
        assert before.best_bid_qty_base == 500
        assert seeded_book.snapshot(10).best_bid_qty_base == 1_500

    def test_touch_ticks_is_what_an_aggressive_order_crosses_into(self, seeded_book):
        snapshot = seeded_book.snapshot(10)
        assert snapshot.touch_ticks(Side.BUY) == 10_001
        assert snapshot.touch_ticks(Side.SELL) == 9_999

    def test_depth_truncation(self, seeded_book):
        assert len(seeded_book.snapshot(1).bids) == 1
        assert seeded_book.snapshot(1).depth_base(Side.BUY) == 500
        assert seeded_book.snapshot(None).depth_base(Side.BUY) == 1_500
