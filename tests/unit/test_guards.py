"""Order value limits must hold for both decision and arrival priced children."""

from __future__ import annotations

from copy import deepcopy

import pytest

from tradeforge.application.harness import ExecutionHarness, RunRequest
from tradeforge.domain.exceptions import ConfigurationError
from tradeforge.execution.guards import GuardConfig


@pytest.mark.parametrize(
    "guards",
    [
        {"max_notional": float("nan")},
        {"max_notional": float("inf")},
        {"max_participation": float("nan")},
        {"max_participation": 1.5},
        {"max_child_order_base": 0},
        {"max_open_orders": -1},
        {"max_inventory_base": 0},
        {"kill_switch_enabled": "maybe"},
    ],
)
def test_invalid_guard_config_fails_before_simulation(guards):
    with pytest.raises(ConfigurationError):
        GuardConfig.from_dict({"guards": guards})


def test_explicitly_disabled_kill_switch_remains_supported():
    assert not GuardConfig.from_dict({"guards": {"kill_switch_enabled": False}}).kill_switch_enabled
    with pytest.raises(ConfigurationError, match="finite and positive"):
        GuardConfig(max_notional=float("nan"))


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5, float("nan"), float("inf"), -float("inf")])
def test_invalid_parent_quantity_override_is_rejected(configs, quantity):
    with pytest.raises(ConfigurationError, match="quantity_base.*positive integer"):
        ExecutionHarness(configs).parent_order(RunRequest(quantity_base=quantity))


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5, float("nan"), float("inf"), -float("inf")])
def test_invalid_configured_parent_quantity_is_rejected(configs, quantity):
    cfg = deepcopy(configs)
    cfg["execution"]["parent_order"]["quantity_base"] = quantity
    with pytest.raises(ConfigurationError, match="quantity_base"):
        ExecutionHarness(cfg).parent_order(RunRequest())


def test_parent_quantity_defaults_only_when_the_override_is_absent(configs):
    harness = ExecutionHarness(configs)
    configured = configs["execution"]["parent_order"]["quantity_base"]
    assert harness.parent_order(RunRequest()).quantity_base == configured
    assert harness.parent_order(RunRequest(quantity_base=100)).quantity_base == 100


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
