"""Domain invariants: prices, quantities, order states, capability gating."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tradeforge.domain.capability import Capability, describe, require, supports
from tradeforge.domain.enums import (
    DataType,
    EventType,
    OrderStatus,
    OrderType,
    Side,
    TimeInForce,
)
from tradeforge.domain.exceptions import (
    DataCapabilityError,
    InstrumentError,
    OrderStateError,
)
from tradeforge.domain.fills import markout_bps, signed_cost_bps
from tradeforge.domain.instrument import InstrumentSpec
from tradeforge.domain.orders import ChildOrder


class TestInstrument:
    def test_price_to_ticks_rounds_half_up(self, instrument):
        assert instrument.price_to_ticks("100.00") == 10_000
        assert instrument.price_to_ticks("100.005") == 10_001
        assert instrument.price_to_ticks(Decimal("99.994")) == 9_999

    def test_tick_round_trip_is_exact_for_valid_ticks(self, instrument):
        for ticks in (5_000, 9_999, 10_000, 20_000):
            assert instrument.price_to_ticks(instrument.ticks_to_decimal(ticks)) == ticks

    def test_float_conversion_is_labelled_lossy(self, instrument):
        # The float path exists for reporting only; the exact path is Decimal.
        assert instrument.ticks_to_float(10_000) == pytest.approx(100.0)
        assert instrument.ticks_to_decimal(10_000) == Decimal("100.00")

    def test_price_band_is_enforced(self, instrument):
        instrument.validate_price_ticks(10_000)
        with pytest.raises(InstrumentError):
            instrument.validate_price_ticks(4_999)
        with pytest.raises(InstrumentError):
            instrument.validate_price_ticks(20_001)

    def test_lot_rounding_rounds_down(self, instrument):
        assert instrument.round_to_lot(1_005) == 1_000
        assert instrument.round_to_lot(9) == 0

    def test_notional_is_exact(self, instrument):
        assert instrument.notional(10_000, 250) == Decimal("25000.00")

    def test_invalid_spec_is_rejected(self):
        with pytest.raises(InstrumentError):
            InstrumentSpec("X", Decimal("0"), 1, "USD", 1, 2)
        with pytest.raises(InstrumentError):
            InstrumentSpec("X", Decimal("0.01"), 1, "USD", 10, 10)


class TestSide:
    def test_sign_convention(self):
        assert Side.BUY.sign == 1
        assert Side.SELL.sign == -1

    def test_opposite_is_involutive(self):
        assert Side.BUY.opposite is Side.SELL
        assert Side.SELL.opposite is Side.BUY


class TestSignConventions:
    """Positive cost = worse, everywhere. These tests pin the convention."""

    def test_signed_cost_positive_when_buying_above_benchmark(self):
        assert signed_cost_bps(Side.BUY, 10_010, 10_000) > 0

    def test_signed_cost_positive_when_selling_below_benchmark(self):
        assert signed_cost_bps(Side.SELL, 9_990, 10_000) > 0

    def test_signed_cost_negative_when_buying_below_benchmark(self):
        assert signed_cost_bps(Side.BUY, 9_990, 10_000) < 0

    def test_markout_positive_when_buy_is_adversely_selected(self):
        # Bought at 100.00, mid later fell to 99.90 -> adverse.
        assert markout_bps(Side.BUY, 10_000.0, 9_990.0) > 0

    def test_markout_positive_when_sell_is_adversely_selected(self):
        # Sold at 100.00, mid later rose to 100.10 -> adverse.
        assert markout_bps(Side.SELL, 10_000.0, 10_010.0) > 0

    def test_markout_rejects_non_positive_price(self):
        with pytest.raises(ValueError):
            markout_bps(Side.BUY, 0.0, 10_000.0)


class TestOrderStateMachine:
    def _order(self) -> ChildOrder:
        return ChildOrder(
            order_id=1,
            client_order_id="c1",
            symbol="TEST",
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            quantity_base=100,
            time_in_force=TimeInForce.DAY,
            created_ns=0,
        )

    def test_new_can_be_accepted_or_rejected(self):
        order = self._order()
        order.transition_to(OrderStatus.ACCEPTED, at_ns=1)
        assert order.working_ns == 1

    def test_new_cannot_jump_straight_to_filled(self):
        order = self._order()
        with pytest.raises(OrderStateError):
            order.transition_to(OrderStatus.FILLED, at_ns=1)

    def test_terminal_states_are_final(self):
        order = self._order()
        order.transition_to(OrderStatus.ACCEPTED, at_ns=1)
        order.transition_to(OrderStatus.CANCELLED, at_ns=2)
        assert order.terminal_ns == 2
        with pytest.raises(OrderStateError):
            order.transition_to(OrderStatus.ACCEPTED, at_ns=3)

    def test_partial_fill_then_full(self):
        order = self._order()
        order.transition_to(OrderStatus.ACCEPTED, at_ns=1)
        order.apply_fill(40, at_ns=2)
        assert order.status is OrderStatus.PARTIALLY_FILLED
        assert order.remaining_base == 60
        order.apply_fill(60, at_ns=3)
        assert order.status is OrderStatus.FILLED
        assert order.remaining_base == 0

    def test_overfill_is_rejected(self):
        order = self._order()
        order.transition_to(OrderStatus.ACCEPTED, at_ns=1)
        with pytest.raises(OrderStateError):
            order.apply_fill(101, at_ns=2)

    def test_non_positive_fill_is_rejected(self):
        order = self._order()
        order.transition_to(OrderStatus.ACCEPTED, at_ns=1)
        with pytest.raises(OrderStateError):
            order.apply_fill(0, at_ns=2)


class TestParentOrder:
    def test_completion_rate(self, parent_order):
        assert parent_order.completion_rate == 0.0
        parent_order.filled_base = 2_500
        assert parent_order.completion_rate == pytest.approx(0.25)

    def test_duration(self, parent_order):
        assert parent_order.duration_ns == 1_799_000_000_000


class TestCapabilityGating:
    """Capability claims are refusals, not warnings."""

    @pytest.mark.parametrize(
        "data_type,capability,expected",
        [
            (DataType.L1, Capability.BEST_BID_ASK, True),
            (DataType.L1, Capability.DEPTH_CURVE, False),
            (DataType.L2_MBP, Capability.DEPTH_CURVE, True),
            (DataType.L2_MBP, Capability.MBP_RECONSTRUCTION, True),
            (DataType.L2_MBP, Capability.EXACT_QUEUE, False),
            (DataType.L2_MBP, Capability.MBO_RECONSTRUCTION, False),
            (DataType.L3_MBO, Capability.EXACT_QUEUE, True),
            (DataType.L3_MBO, Capability.MBO_RECONSTRUCTION, True),
            (DataType.L3_MBO, Capability.ORDER_LEVEL_CANCEL, True),
        ],
    )
    def test_supports(self, data_type, capability, expected):
        assert supports(data_type, capability) is expected

    def test_a_higher_tier_does_not_grant_exact_queue_implicitly(self):
        """EXACT_QUEUE is tier-exact, not 'at least': the check is not monotone."""
        assert supports(DataType.L3_MBO, Capability.EXACT_QUEUE)
        assert not supports(DataType.L2_MBP, Capability.EXACT_QUEUE)

    def test_require_raises_with_an_honest_message(self):
        with pytest.raises(DataCapabilityError) as excinfo:
            require(DataType.L2_MBP, Capability.EXACT_QUEUE, context="test")
        message = str(excinfo.value)
        assert "does not fabricate market detail" in message
        assert "L3_MBO" in message
        assert "test" in message

    def test_require_passes_when_supported(self):
        require(DataType.L3_MBO, Capability.EXACT_QUEUE, context="test")

    def test_describe_lists_what_is_available(self):
        described = describe(DataType.L2_MBP)
        assert described["depth_curve"] is True
        assert described["exact_queue"] is False
        assert described["best_bid_ask"] is True


class TestEventSemantics:
    def test_only_book_events_mutate_the_book(self):
        assert EventType.ADD.mutates_book
        assert EventType.TRADE.mutates_book
        assert EventType.CLEAR.mutates_book
        assert not EventType.HALT.mutates_book
        assert not EventType.RESUME.mutates_book
