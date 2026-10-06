"""Commission minima belong to the order, independently of venue fees."""

from __future__ import annotations

from decimal import Decimal

import pytest

from tradeforge.costs.fees import FeeConfig, FeeModel
from tradeforge.domain.enums import LiquidityFlag


@pytest.mark.parametrize(
    ("config", "liquidity", "expected"),
    [
        (
            FeeConfig(taker_fee_bps=10, commission_per_share=0.01, min_commission_per_order=5),
            LiquidityFlag.TAKER,
            Decimal("15"),
        ),
        (
            FeeConfig(maker_rebate_bps=10, commission_per_share=0.01, min_commission_per_order=5),
            LiquidityFlag.MAKER,
            Decimal("-5"),
        ),
        (
            FeeConfig(maker_rebate_bps=5, commission_per_share=0.01, min_commission_per_order=5),
            LiquidityFlag.MAKER,
            Decimal("0"),
        ),
    ],
)
def test_commission_minimum_does_not_floor_exchange_fees(config, liquidity, expected):
    assert (
        FeeModel(config).fee(notional=Decimal("10000"), liquidity=liquidity, quantity_base=100)
        == expected
    )


def test_partial_fills_charge_only_the_remaining_order_commission():
    model = FeeModel(FeeConfig(commission_per_share=0.01, min_commission_per_order=5))
    before = 0
    charges = []
    for quantity, expected in [(100, Decimal("5")), (200, Decimal("0")), (500, Decimal("3"))]:
        charge = model.fee(
            notional=Decimal("100") * quantity,
            liquidity=LiquidityFlag.TAKER,
            quantity_base=quantity,
            filled_before_base=before,
        )
        assert charge == expected
        charges.append(charge)
        before += quantity
    assert sum(charges) == Decimal("8")


def test_fee_quotes_are_stateless_and_bps_use_the_same_order_history():
    model = FeeModel(FeeConfig(commission_per_share=0.01, min_commission_per_order=5))
    quote = {
        "notional": Decimal("10000"),
        "liquidity": LiquidityFlag.TAKER,
        "quantity_base": 100,
        "filled_before_base": 100,
    }
    assert model.fee(**quote) == model.fee(**quote) == Decimal("0")
    assert model.fee_bps(**quote) == 0


def test_zero_minimum_preserves_per_fill_fees_and_rebates():
    model = FeeModel(FeeConfig(maker_rebate_bps=0.2, commission_per_share=0.0005))
    assert model.fee(
        notional=Decimal("10000"),
        liquidity=LiquidityFlag.MAKER,
        quantity_base=200,
        filled_before_base=500,
    ) == Decimal("-0.1")


@pytest.mark.parametrize("before", [-1, True, 1.5])
def test_invalid_order_fill_history_is_rejected(before):
    with pytest.raises(ValueError, match="filled_before_base"):
        FeeModel(FeeConfig()).fee(
            notional=Decimal("10000"),
            liquidity=LiquidityFlag.TAKER,
            quantity_base=100,
            filled_before_base=before,
        )


def test_no_fill_does_not_charge_a_minimum():
    model = FeeModel(FeeConfig(min_commission_per_order=5))
    assert model.fee(notional=Decimal("0"), liquidity=LiquidityFlag.TAKER) == 0


def test_cost_description_records_the_order_minimum():
    model = FeeModel(FeeConfig(min_commission_per_order=5))
    assert model.describe()["min_commission_per_order"] == 5
