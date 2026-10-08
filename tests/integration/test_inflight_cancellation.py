"""Window cancellation must also reach children whose venue arrival is pending."""

from __future__ import annotations

import copy
import csv

import pytest

from tradeforge.application.harness import ExecutionHarness, RunRequest
from tradeforge.domain.enums import OrderStatus


@pytest.mark.parametrize(
    ("end_action", "latency_ns", "expected_filled", "expected_cancelled"),
    [
        ("cancel", 2_000_000_000, 0, 1),
        ("sweep_marketable", 2_000_000_000, 100, 1),
        ("leave", 2_000_000_000, 50, 0),
        ("sweep_marketable", 1_000_000_000, 100, 0),
    ],
)
@pytest.mark.parametrize("max_open_orders", [1, 10])
def test_deadline_cancellation_reconciles_inflight_children(
    configs, tmp_path, end_action, latency_ns, expected_filled, expected_cancelled, max_open_orders
):
    effective = copy.deepcopy(configs)
    options = effective["market_data"]["source"]["options"]
    start = options["start_time_ns"]
    path = tmp_path / "inflight-deadline.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "sequence_id",
                "exchange_timestamp_ns",
                "symbol",
                "event_type",
                "side",
                "price_ticks",
                "quantity_base",
            ]
        )
        for sequence, (offset, event, side, price, quantity) in enumerate(
            [
                (0, "ADD", "BUY", 9999, 300),
                (0, "ADD", "SELL", 10001, 300),
                (1_000_000_000, "TRADE", "SELL", 9999, 100),
                (2_000_000_000, "ADD", "BUY", 9998, 300),
                (4_000_000_000, "ADD", "BUY", 9998, 1),
                (5_000_000_000, "ADD", "BUY", 9998, 1),
            ]
        ):
            writer.writerow([sequence, start + offset, "SYNTH", event, side, price, quantity])
    effective["market_data"]["source"]["adapter"] = "normalized"
    options["path"] = str(path)
    effective["execution"]["parent_order"].update(start_offset_ns=0, end_offset_ns=3_000_000_000)
    effective["execution"]["policies"]["twap"]["end_of_window"] = end_action
    effective["replay"]["guards"]["max_participation"] = 1.0
    effective["replay"]["guards"]["max_open_orders"] = max_open_orders
    effective["costs"]["tca"]["markout_horizons_ns"] = [3_000_000_000]

    context = ExecutionHarness(effective).run(
        RunRequest(
            policy="twap", style="aggressive", quantity_base=100, n_slices=2, latency_ns=latency_ns
        )
    )
    result = context.result
    assert result is not None
    assert context.validation is not None and context.validation.n_issues == 0
    assert result.filled_base == expected_filled
    assert result.filled_base <= result.requested_base
    assert result.n_cancels == expected_cancelled
    assert sum(order.status is OrderStatus.CANCELLED for order in result.child_orders) == (
        expected_cancelled
    )
    assert sum(fill.quantity_base for fill in result.fills) == expected_filled
    first_child = result.child_orders[0]
    assert first_child.created_ns == start + 2_000_000_000
    if expected_cancelled:
        assert first_child.terminal_ns == result.end_ns
        assert first_child.working_ns is None
        assert first_child.filled_base == 0
        assert all(fill.client_order_id != first_child.client_order_id for fill in result.fills)
    elif latency_ns == 1_000_000_000:
        assert result.fills[0].timestamp_ns == result.end_ns
    else:
        assert result.fills[0].timestamp_ns == start + 4_000_000_000
