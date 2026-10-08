"""Invalid fee inputs must not become artificial gains or non-finite reports."""

from decimal import Decimal

import pytest

from tradeforge.costs.fees import FeeConfig, FeeModel
from tradeforge.domain.enums import LiquidityFlag


@pytest.mark.parametrize(
    "field",
    [
        "taker_fee_bps",
        "maker_fee_bps",
        "maker_rebate_bps",
        "commission_per_share",
        "min_commission_per_order",
    ],
)
@pytest.mark.parametrize(
    "value", [-0.01, float("nan"), float("inf"), True, "-1e-9999", Decimal("-1e-9999")]
)
def test_invalid_parameters_fail_at_direct_construction_and_config_loading(field, value):
    with pytest.raises(ValueError, match=field):
        FeeConfig(**{field: value})
    with pytest.raises(ValueError, match=field):
        FeeConfig.from_dict({"fees": {field: value}})


@pytest.mark.parametrize("value", ["NaN", "sNaN", "Infinity", "-Infinity", "1e309", "-1", None])
def test_invalid_serialized_commissions_are_rejected(value):
    with pytest.raises(ValueError, match="commission_per_share"):
        FeeConfig.from_dict({"fees": {"commission_per_share": value}})


@pytest.mark.parametrize("payload", [False, [], 1, {"fees": None}, {"fees": []}])
def test_malformed_cost_mappings_do_not_silently_use_zero_fees(payload):
    with pytest.raises(TypeError, match="mapping"):
        FeeConfig.from_dict(payload)


def test_numeric_strings_are_normalized_and_rebates_can_make_a_negative_fee():
    config = FeeConfig.from_dict(
        {
            "fees": {
                "maker_fee_bps": "2.5",
                "maker_rebate_bps": "7.5",
                "commission_per_share": "0.01",
                "min_commission_per_order": "5",
            }
        }
    )
    assert config.maker_fee_bps == 2.5
    assert config.min_commission_per_order == 5.0
    assert FeeModel(config).fee(
        notional=Decimal("20000"), liquidity=LiquidityFlag.MAKER, quantity_base=100
    ) == Decimal("-5")


@pytest.mark.parametrize(
    "notional",
    [Decimal("NaN"), Decimal("sNaN"), Decimal("Infinity"), Decimal("-1"), 100.0, "100"],
)
def test_nonfinite_negative_or_nondecimal_notionals_are_rejected(notional):
    model = FeeModel(FeeConfig())
    for quote in (model.fee, model.fee_bps):
        with pytest.raises(ValueError, match="notional"):
            quote(notional=notional, liquidity=LiquidityFlag.TAKER, quantity_base=100)


@pytest.mark.parametrize("quantity", [-1, True, 1.5, "100", Decimal("100")])
def test_invalid_fill_quantities_are_not_coerced(quantity):
    model = FeeModel(FeeConfig(min_commission_per_order=5))
    for quote in (model.fee, model.fee_bps):
        with pytest.raises(ValueError, match="quantity_base"):
            quote(notional=Decimal("10000"), liquidity=LiquidityFlag.TAKER, quantity_base=quantity)


@pytest.mark.parametrize("liquidity", ["MAKER", None])
def test_unrecognized_liquidity_does_not_become_a_fee_assumption(liquidity):
    with pytest.raises(ValueError, match="liquidity"):
        FeeModel(FeeConfig()).fee(notional=Decimal("10000"), liquidity=liquidity, quantity_base=100)


def test_empty_configs_and_zero_notional_quotes_remain_valid():
    assert FeeConfig.from_dict(None) == FeeConfig.from_dict({}) == FeeConfig()
    model = FeeModel(FeeConfig())
    assert model.fee(notional=Decimal("0"), liquidity=LiquidityFlag.UNKNOWN) == 0
    assert model.fee_bps(notional=Decimal("0"), liquidity=LiquidityFlag.UNKNOWN) == 0
