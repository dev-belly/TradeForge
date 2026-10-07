"""Commission minima belong to the order, independently of venue fees."""

from __future__ import annotations

import random
from decimal import ROUND_DOWN, Decimal, Inexact, Rounded, localcontext
from fractions import Fraction

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


@pytest.mark.parametrize(
    ("config", "notional", "quantity", "before", "liquidity", "expected"),
    [
        (
            FeeConfig(commission_per_share=0.01, min_commission_per_order=5),
            Decimal("9900"),
            99,
            74001,
            LiquidityFlag.TAKER,
            Decimal("0.99"),
        ),
        (
            FeeConfig(maker_fee_bps=0.3, maker_rebate_bps=0.2),
            Decimal("10000"),
            0,
            0,
            LiquidityFlag.MAKER,
            Decimal("0.1"),
        ),
        (
            FeeConfig(taker_fee_bps=10),
            Decimal("39.12"),
            0,
            0,
            LiquidityFlag.TAKER,
            Decimal("0.03912"),
        ),
    ],
)
def test_fee_amounts_ignore_caller_precision_exponents_and_traps(
    config, notional, quantity, before, liquidity, expected
):
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        context.Emax = 1
        context.Emin = -1
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        assert (
            FeeModel(config).fee(
                notional=notional,
                liquidity=liquidity,
                quantity_base=quantity,
                filled_before_base=before,
            )
            == expected
        )
        assert context.prec == 2 and context.Emax == 1 and context.Emin == -1
        assert context.rounding == ROUND_DOWN and context.traps[Inexact] and context.traps[Rounded]


def test_fee_preserves_digits_beyond_default_decimal_precision():
    notional = Decimal("1." + "0" * 60 + "1")
    model = FeeModel(FeeConfig(taker_fee_bps=10000))
    assert model.fee(notional=notional, liquidity=LiquidityFlag.TAKER) == notional


def test_bps_reporting_uses_an_exact_ratio_before_float_conversion():
    model = FeeModel(FeeConfig(commission_per_share=0.01, min_commission_per_order=5))
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        assert model.fee_bps(
            notional=Decimal("12345.67"),
            liquidity=LiquidityFlag.TAKER,
            quantity_base=100,
            filled_before_base=1000,
        ) == float(Fraction(10000) / Fraction("12345.67"))


def test_partial_fill_partitions_reconcile_to_an_independent_order_total():
    randomizer = random.Random(20261006)
    for _ in range(400):
        config = FeeConfig(
            taker_fee_bps=1.5,
            maker_fee_bps=0.3,
            maker_rebate_bps=0.2,
            commission_per_share=randomizer.choice([0.0, 0.0005, 0.01]),
            min_commission_per_order=randomizer.choice([0.0, 0.5, 5.0]),
        )
        model = FeeModel(config)
        total = randomizer.randint(1, 25000)
        remaining = total
        before = 0
        venue = Fraction(0)
        quoted = Fraction(0)
        while remaining:
            quantity = randomizer.randint(1, remaining)
            cents = randomizer.randint(1, 100000) * quantity
            notional = Decimal((0, Decimal(cents).as_tuple().digits, -2))
            liquidity = randomizer.choice([LiquidityFlag.MAKER, LiquidityFlag.TAKER])
            rate = Fraction("1.5") if liquidity is LiquidityFlag.TAKER else Fraction("0.1")
            venue += Fraction(notional) * rate / 10000
            quoted += Fraction(
                model.fee(
                    notional=notional,
                    liquidity=liquidity,
                    quantity_base=quantity,
                    filled_before_base=before,
                )
            )
            before += quantity
            remaining -= quantity
        commission = max(
            Fraction(str(config.commission_per_share)) * total,
            Fraction(str(config.min_commission_per_order)),
        )
        assert quoted == venue + commission
