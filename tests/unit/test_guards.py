"""Order value limits must hold for both decision and arrival priced children."""

from __future__ import annotations

from copy import deepcopy

import pytest

from tradeforge.application.harness import ExecutionHarness, RunRequest


@pytest.mark.parametrize("style", ["aggressive", "passive"])
def test_max_notional_rejects_unpriced_policy_orders_after_pricing(configs, style):
    cfg = deepcopy(configs)
    cfg["replay"]["guards"]["max_notional"] = 1.0

    result = (
        ExecutionHarness(cfg)
        .run(RunRequest(policy="twap", style=style, n_slices=2, quantity_base=100, seed=7))
        .result
    )

    assert result is not None
    assert result.n_child_orders == 2
    assert result.n_rejects == 2
    assert result.filled_base == 0
    assert result.notional == 0
    assert all("notional" in report.reason for report in result.reports)
