"""Simulated matching against an observable book.

The distinction this file protects: crossing the book is a *decision*, and it is
not the same code path as reconstructing what happened historically.
"""

from __future__ import annotations

import pytest

from tradeforge.domain.enums import LiquidityFlag, OrderType, Side, TimeInForce
from tradeforge.domain.exceptions import MatchingError
from tradeforge.matching import is_marketable, match_order, resting_price_ticks


class TestMatchOrder:
    def test_market_buy_takes_the_best_ask_first(self, seeded_book):
        result = match_order(seeded_book.snapshot(10), side=Side.BUY, quantity_base=250)
        assert result.filled_base == 250
        assert result.avg_price_ticks == 10_001
        assert result.liquidity_flag is LiquidityFlag.TAKER
        assert result.levels_crossed == 1

    def test_walking_multiple_levels_prices_each_one(self, seeded_book):
        # ask 10001 x 400, 10002 x 900 -> 600 fills across two levels.
        result = match_order(seeded_book.snapshot(10), side=Side.BUY, quantity_base=600)
        assert result.filled_base == 600
        assert result.levels_crossed == 2
        assert result.worst_price_ticks == 10_002
        expected = (400 * 10_001 + 200 * 10_002) / 600
        assert result.avg_price_ticks == pytest.approx(expected)

    def test_limit_price_prevents_paying_through(self, seeded_book):
        result = match_order(
            seeded_book.snapshot(10),
            side=Side.BUY,
            quantity_base=600,
            order_type=OrderType.LIMIT,
            limit_price_ticks=10_001,
        )
        assert result.filled_base == 400
        assert result.remaining_base == 200
        assert result.worst_price_ticks == 10_001

    def test_ioc_keeps_the_crossed_part_and_cancels_the_rest(self, seeded_book):
        result = match_order(
            seeded_book.snapshot(10),
            side=Side.BUY,
            quantity_base=600,
            order_type=OrderType.LIMIT,
            limit_price_ticks=10_001,
            time_in_force=TimeInForce.IOC,
        )
        assert result.filled_base == 400
        # IOC reports no remainder: the unfilled part is cancelled, not rested.
        assert result.remaining_base == 0

    def test_fok_is_all_or_nothing(self, seeded_book):
        result = match_order(
            seeded_book.snapshot(10),
            side=Side.BUY,
            quantity_base=600,
            order_type=OrderType.LIMIT,
            limit_price_ticks=10_001,
            time_in_force=TimeInForce.FOK,
        )
        assert result.filled_base == 0
        assert result.matched == ()
        assert result.remaining_base == 600

    def test_fok_fills_completely_when_depth_allows(self, seeded_book):
        result = match_order(
            seeded_book.snapshot(10),
            side=Side.BUY,
            quantity_base=400,
            order_type=OrderType.LIMIT,
            limit_price_ticks=10_001,
            time_in_force=TimeInForce.FOK,
        )
        assert result.filled_base == 400

    def test_max_levels_crossed_caps_the_sweep(self, seeded_book):
        result = match_order(
            seeded_book.snapshot(10),
            side=Side.BUY,
            quantity_base=2_000,
            max_levels_crossed=1,
        )
        assert result.levels_crossed == 1
        assert result.filled_base == 400

    def test_sell_walks_the_bid_side(self, seeded_book):
        result = match_order(seeded_book.snapshot(10), side=Side.SELL, quantity_base=600)
        assert result.filled_base == 600
        assert result.worst_price_ticks == 9_998
        assert result.avg_price_ticks < 9_999

    def test_empty_book_fills_nothing(self):
        from tradeforge.domain.book import BookSnapshot

        empty = BookSnapshot(timestamp_ns=0, sequence_id=0, symbol="TEST")
        result = match_order(empty, side=Side.BUY, quantity_base=100)
        assert result.filled_base == 0
        assert result.remaining_base == 100

    def test_non_positive_quantity_is_rejected(self, seeded_book):
        with pytest.raises(MatchingError):
            match_order(seeded_book.snapshot(10), side=Side.BUY, quantity_base=0)

    def test_limit_order_without_a_price_is_rejected(self, seeded_book):
        with pytest.raises(MatchingError):
            match_order(
                seeded_book.snapshot(10),
                side=Side.BUY,
                quantity_base=100,
                order_type=OrderType.LIMIT,
            )

    def test_market_order_never_fills_at_the_mid(self, seeded_book):
        """A market buy pays the ask, not the midpoint."""
        snapshot = seeded_book.snapshot(10)
        result = match_order(snapshot, side=Side.BUY, quantity_base=100)
        assert result.avg_price_ticks == snapshot.best_ask_ticks
        assert result.avg_price_ticks > snapshot.mid_ticks


class TestIsMarketable:
    def test_buy_at_or_through_the_ask(self, seeded_book):
        snapshot = seeded_book.snapshot(10)
        assert is_marketable(snapshot, Side.BUY, 10_001)
        assert is_marketable(snapshot, Side.BUY, 10_050)
        assert not is_marketable(snapshot, Side.BUY, 10_000)

    def test_sell_at_or_through_the_bid(self, seeded_book):
        snapshot = seeded_book.snapshot(10)
        assert is_marketable(snapshot, Side.SELL, 9_999)
        assert is_marketable(snapshot, Side.SELL, 9_500)
        assert not is_marketable(snapshot, Side.SELL, 10_000)

    def test_none_price_means_market(self, seeded_book):
        assert is_marketable(seeded_book.snapshot(10), Side.BUY, None)


class TestRestingPrice:
    def test_passive_buy_joins_the_bid_not_the_ask(self, seeded_book):
        """The bug this pins: posting a passive BUY on the ask side."""
        snapshot = seeded_book.snapshot(10)
        assert resting_price_ticks(snapshot, Side.BUY, 0) == 9_999
        assert resting_price_ticks(snapshot, Side.BUY, 1) == 9_998

    def test_passive_sell_joins_the_ask(self, seeded_book):
        snapshot = seeded_book.snapshot(10)
        assert resting_price_ticks(snapshot, Side.SELL, 0) == 10_001
        assert resting_price_ticks(snapshot, Side.SELL, 1) == 10_002

    def test_empty_side_returns_none(self):
        from tradeforge.domain.book import BookSnapshot

        empty = BookSnapshot(timestamp_ns=0, sequence_id=0, symbol="TEST")
        assert resting_price_ticks(empty, Side.BUY, 0) is None
        assert resting_price_ticks(empty, Side.SELL, 0) is None
