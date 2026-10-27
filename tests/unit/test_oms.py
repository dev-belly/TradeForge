"""Rejected fills must leave the OMS journal, inventory and identifiers intact."""

from dataclasses import asdict
from decimal import Decimal

import pytest

from tradeforge.domain.enums import LiquidityFlag, PlacementStyle, Side
from tradeforge.domain.exceptions import OrderStateError
from tradeforge.execution.oms import Oms


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
@pytest.mark.parametrize("already_filled", [0, 40])
@pytest.mark.parametrize("quantity", [0, -1, True, 0.5, float("nan"), float("inf"), 101, None])
def test_rejected_fill_does_not_enter_the_oms_journal(side, already_filled, quantity):
    oms = Oms()
    order = oms.create_child(
        symbol="TEST",
        side=side,
        quantity_base=100,
        created_ns=0,
        parent_order_id="parent",
        slice_index=0,
        style=PlacementStyle.AGGRESSIVE,
    )
    oms.accept(order, at_ns=1)

    def fill(amount, at_ns):
        return oms.fill(
            order,
            quantity_base=amount,
            price_ticks=100,
            at_ns=at_ns,
            liquidity=LiquidityFlag.TAKER,
            fee=Decimal("0"),
        )

    if already_filled:
        fill(already_filled, 2)
    before = (asdict(order), oms.fills, oms.inventory_base, oms.n_open_orders)

    with pytest.raises(OrderStateError):
        fill(quantity, 3)

    assert (asdict(order), oms.fills, oms.inventory_base, oms.n_open_orders) == before
    completed = fill(100 - already_filled, 4)
    assert completed.fill.fill_id == (2 if already_filled else 1)
    assert sum(item.quantity_base for item in oms.fills) == 100
    assert oms.inventory_base == side.sign * 100
    assert oms.n_open_orders == 0
