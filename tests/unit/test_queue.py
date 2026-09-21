"""Queue models.

The single most important test in this file is
`test_price_touch_alone_never_fills`. A model that fills on price touch is the
classic way a backtest invents liquidity that was never there, and the rule is
stated in the design review as non-negotiable.
"""

from __future__ import annotations

import pytest

from tradeforge.domain.enums import CancelAheadPolicy, DataType, QueueMode, Side
from tradeforge.domain.exceptions import ConfigurationError, DataCapabilityError
from tradeforge.queue import (
    ApproximateQueueModel,
    ExactQueueModel,
    create_queue_model,
    queue_mode_from_config,
)


class TestApproximateQueue:
    def test_joins_at_the_back_of_the_visible_level(self):
        model = ApproximateQueueModel()
        model.register(
            "c1", side=Side.BUY, price_ticks=9_999, quantity_base=100, level_size_base=800
        )
        assert model.queue_ahead_base("c1") == 800
        assert model.mode is QueueMode.APPROXIMATE

    def test_price_touch_alone_never_fills(self):
        """The central anti-slop rule: resting is not filling."""
        model = ApproximateQueueModel()
        model.register(
            "c1", side=Side.BUY, price_ticks=9_999, quantity_base=100, level_size_base=800
        )
        # Nothing happened; the level is merely 'at' our price.
        assert model.fillable_base("c1") == 0
        assert model.remaining_base("c1") == 100

    def test_trades_consume_the_queue_before_reaching_us(self):
        model = ApproximateQueueModel()
        model.register(
            "c1", side=Side.BUY, price_ticks=9_999, quantity_base=100, level_size_base=500
        )
        assert model.on_trade(price_ticks=9_999, quantity_base=300) == []
        assert model.queue_ahead_base("c1") == 200
        assert model.on_trade(price_ticks=9_999, quantity_base=150) == []
        assert model.queue_ahead_base("c1") == 50
        fills = model.on_trade(price_ticks=9_999, quantity_base=120)
        assert fills == [("c1", 70)]
        assert model.remaining_base("c1") == 30

    def test_a_trade_on_the_other_side_cannot_consume_our_queue(self):
        """A print against the bid must not consume queue on the ask."""
        model = ApproximateQueueModel()
        model.register(
            "c1", side=Side.SELL, price_ticks=10_001, quantity_base=100, level_size_base=400
        )
        # Same price, wrong side: our ask-side queue is untouched.
        assert model.on_trade(price_ticks=10_001, quantity_base=900, side=Side.BUY) == []
        assert model.queue_ahead_base("c1") == 400

    def test_a_trade_exactly_consuming_the_queue_does_not_reach_us(self):
        """Exhausting the queue is not the same as filling our order.

        The trade that clears the last share ahead of us has already been fully
        allocated. Reaching us requires quantity beyond the queue.
        """
        model = ApproximateQueueModel()
        model.register(
            "c1", side=Side.SELL, price_ticks=10_001, quantity_base=100, level_size_base=400
        )
        assert model.on_trade(price_ticks=10_001, quantity_base=400, side=Side.SELL) == []
        assert model.queue_ahead_base("c1") == 0
        assert model.remaining_base("c1") == 100

    def test_the_correct_side_consumes_the_queue(self):
        model = ApproximateQueueModel()
        model.register(
            "c1", side=Side.SELL, price_ticks=10_001, quantity_base=100, level_size_base=400
        )
        assert model.on_trade(price_ticks=10_001, quantity_base=500, side=Side.SELL) == [
            ("c1", 100)
        ]
        # A fully filled order is released, so it no longer has a position.
        with pytest.raises(KeyError):
            model.remaining_base("c1")

    @pytest.mark.parametrize(
        "policy,removed,expected_ahead",
        [
            (CancelAheadPolicy.OPTIMISTIC, 400, 500),  # nothing ahead was pulled
            (CancelAheadPolicy.CONSERVATIVE, 400, 100),  # all of it was ahead
        ],
    )
    def test_cancel_policies_bracket_the_unknown(self, policy, removed, expected_ahead):
        model = ApproximateQueueModel(policy)
        model.register(
            "c1", side=Side.BUY, price_ticks=9_999, quantity_base=100, level_size_base=500
        )
        model.on_size_decrease(price_ticks=9_999, removed_base=removed, level_size_before=900)
        assert model.queue_ahead_base("c1") == expected_ahead

    def test_neutral_policy_attributes_proportionally(self):
        model = ApproximateQueueModel(CancelAheadPolicy.NEUTRAL)
        model.register(
            "c1", side=Side.BUY, price_ticks=9_999, quantity_base=100, level_size_base=500
        )
        # We are 500 of a 1000-share level, so half of a 400 shrink is ours.
        model.on_size_decrease(price_ticks=9_999, removed_base=400, level_size_before=1_000)
        assert model.queue_ahead_base("c1") == 300

    def test_unknown_order_raises(self):
        model = ApproximateQueueModel()
        with pytest.raises(KeyError):
            model.queue_ahead_base("nope")

    def test_release_removes_the_position(self):
        model = ApproximateQueueModel()
        model.register(
            "c1", side=Side.BUY, price_ticks=9_999, quantity_base=100, level_size_base=500
        )
        model.release("c1")
        with pytest.raises(KeyError):
            model.queue_ahead_base("c1")


class TestExactQueue:
    def test_requires_l3_and_refuses_l2(self):
        with pytest.raises(DataCapabilityError) as excinfo:
            ExactQueueModel(DataType.L2_MBP)
        assert "L3_MBO" in str(excinfo.value)

    def test_exact_queue_is_the_sum_of_orders_ahead(self):
        model = ExactQueueModel(DataType.L3_MBO)
        model.register_with_queue(
            "c1",
            side=Side.BUY,
            price_ticks=9_999,
            quantity_base=100,
            orders_ahead=[(11, 200), (12, 300), (13, 150)],
        )
        assert model.queue_ahead_base("c1") == 650
        assert model.mode is QueueMode.EXACT

    def test_fifo_consumption_named_by_order_id(self):
        model = ExactQueueModel(DataType.L3_MBO)
        model.register_with_queue(
            "c1",
            side=Side.BUY,
            price_ticks=9_999,
            quantity_base=100,
            orders_ahead=[(11, 200), (12, 300)],
        )
        # The front order is partially executed.
        assert (
            model.on_trade(price_ticks=9_999, quantity_base=120, order_id=11, side=Side.BUY) == []
        )
        assert model.queue_ahead_base("c1") == 380
        # It is exhausted; we are next in line.
        assert model.on_trade(price_ticks=9_999, quantity_base=80, order_id=11, side=Side.BUY) == []
        assert model.queue_ahead_base("c1") == 300

    def test_execution_reaches_us_once_the_queue_is_empty(self):
        model = ExactQueueModel(DataType.L3_MBO)
        model.register_with_queue(
            "c1",
            side=Side.BUY,
            price_ticks=9_999,
            quantity_base=100,
            orders_ahead=[],
        )
        assert model.on_trade(price_ticks=9_999, quantity_base=40, order_id=99, side=Side.BUY) == [
            ("c1", 40)
        ]
        assert model.remaining_base("c1") == 60

    def test_cancel_without_order_identity_is_refused(self):
        model = ExactQueueModel(DataType.L3_MBO)
        model.register_with_queue(
            "c1",
            side=Side.BUY,
            price_ticks=9_999,
            quantity_base=100,
            orders_ahead=[(11, 200)],
        )
        with pytest.raises(DataCapabilityError) as excinfo:
            model.on_size_decrease(price_ticks=9_999, removed_base=50, level_size_before=300)
        assert "order identity" in str(excinfo.value)


class TestQueueFactory:
    def test_exact_with_l2_is_refused_at_construction(self):
        with pytest.raises(DataCapabilityError):
            create_queue_model(QueueMode.EXACT, DataType.L2_MBP)

    def test_exact_with_l3_is_allowed(self):
        model = create_queue_model(QueueMode.EXACT, DataType.L3_MBO)
        assert model.mode is QueueMode.EXACT

    def test_config_rejects_exact_on_l2(self):
        with pytest.raises(ConfigurationError) as excinfo:
            queue_mode_from_config({"queue": {"mode": "exact"}}, DataType.L2_MBP)
        assert "requires L3_MBO" in str(excinfo.value)

    def test_config_accepts_approximate_on_l2(self):
        mode, policy = queue_mode_from_config(
            {"queue": {"mode": "approximate", "cancel_ahead_policy": "optimistic"}},
            DataType.L2_MBP,
        )
        assert mode is QueueMode.APPROXIMATE
        assert policy is CancelAheadPolicy.OPTIMISTIC
