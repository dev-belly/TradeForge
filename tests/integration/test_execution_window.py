"""Warm-up events rebuild the book without becoming execution-window volume."""

from __future__ import annotations

import copy
import csv

import pytest

from tradeforge.application.harness import ExecutionHarness, RunRequest


@pytest.fixture
def window_configs(configs, tmp_path):
    effective = copy.deepcopy(configs)
    options = effective["market_data"]["source"]["options"]
    start = options["start_time_ns"]
    path = tmp_path / "synthetic-warmup.csv"
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
                (250_000_000, "TRADE", "SELL", 9999, 100),
                (1_000_000_000, "ADD", "BUY", 10000, 300),
                (2_000_000_000, "TRADE", "SELL", 10000, 100),
                (3_000_000_000, "ADD", "BUY", 9998, 300),
            ]
        ):
            writer.writerow([sequence, start + offset, "SYNTH", event, side, price, quantity])
    effective["market_data"]["source"]["adapter"] = "normalized"
    options["path"] = str(path)
    effective["execution"]["parent_order"].update(
        start_offset_ns=1_000_000_000,
        end_offset_ns=2_000_000_000,
    )
    effective["execution"]["policies"]["twap"]["end_of_window"] = "cancel"
    effective["execution"]["policies"]["pov"].update(
        end_of_window="cancel",
        min_slice_base=1,
        rebalance_interval_ns=1,
    )
    effective["replay"]["guards"]["max_participation"] = 1.0
    effective["costs"]["tca"]["markout_horizons_ns"] = [1_000_000]
    return effective


@pytest.mark.parametrize("policy", ["twap", "pov"])
def test_delayed_parent_replays_warmup_without_trading_it(window_configs, policy):
    session_start = window_configs["market_data"]["source"]["options"]["start_time_ns"]
    context = ExecutionHarness(window_configs).run(
        RunRequest(
            policy=policy,
            style="aggressive",
            quantity_base=100,
            n_slices=2,
            pov_target_rate=0.5,
            latency_ns=0,
        )
    )
    result = context.result
    assert result is not None
    assert context.outcome.first_timestamp_ns == session_start
    assert context.outcome.n_events == 6
    assert context.validation is not None and context.validation.n_issues == 0
    assert len(result.child_orders) == 1
    assert result.child_orders[0].created_ns == session_start + 2_000_000_000
    assert result.child_orders[0].quantity_base == 50
    assert result.filled_base == 50
    assert result.market_volume_base == 100  # excludes the 100-unit warm-up trade
    assert result.metadata["arrival_ns"] == session_start + 1_000_000_000
    assert result.arrival_mid_ticks == 10000.5  # warm-up mid was 10000


def test_stream_ending_before_parent_start_has_no_execution_benchmark(window_configs):
    window_configs["execution"]["parent_order"]["start_offset_ns"] = 10_000_000_000
    context = ExecutionHarness(window_configs).run(
        RunRequest(policy="twap", style="aggressive", quantity_base=100, n_slices=2)
    )
    result = context.result
    assert result is not None
    assert context.outcome.n_events == 6
    assert result.child_orders == () and result.fills == ()
    assert result.market_volume_base == 0
    assert result.arrival_mid_ticks is None
    assert result.terminal_mid_ticks is None
    assert result.metadata["arrival_ns"] is None


def test_post_window_data_reaches_markouts_without_changing_execution_metrics(window_configs):
    options = window_configs["market_data"]["source"]["options"]
    start = options["start_time_ns"]
    with open(options["path"], "a", newline="") as handle:
        writer = csv.writer(handle)
        for sequence, (offset, event, side, price, quantity) in enumerate(
            [
                (3_100_000_000, "CANCEL", "SELL", 10001, 300),
                (3_200_000_000, "ADD", "SELL", 10003, 300),
                (3_300_000_000, "TRADE", "SELL", 10000, 100),
                (4_000_000_000, "ADD", "BUY", 9998, 300),
            ],
            start=6,
        ):
            writer.writerow([sequence, start + offset, "SYNTH", event, side, price, quantity])
    window_configs["costs"]["tca"]["markout_horizons_ns"] = [2_000_000_000]
    context = ExecutionHarness(window_configs).run(
        RunRequest(policy="twap", style="aggressive", quantity_base=100, n_slices=2, latency_ns=0)
    )
    result = context.result
    assert result is not None and context.tca is not None
    assert context.outcome.n_events == 10
    assert result.filled_base == 50
    assert result.market_volume_base == 100
    assert result.participation_rate == 0.5
    assert result.terminal_mid_ticks == context.tca.benchmarks.terminal_mid_ticks == 10000.5
    assert context.tca.metrics.participation_rate == 0.5
    assert context.tca.markouts[0].n_measurable == 1


def test_sparse_feed_closes_at_deadline_without_using_future_book(window_configs):
    """A missing end tick must not shift the terminal sweep to the next tick."""
    options = window_configs["market_data"]["source"]["options"]
    session_start = options["start_time_ns"]
    end_ns = session_start + 2_000_000_000
    path = options["path"]

    # Parent: [t+1s, t+2s]. The last in-window quote is at t+1s;
    # the next quote arrives at t+3s. There is no event at t+2s.
    window_configs["execution"]["parent_order"]["end_offset_ns"] = 1_000_000_000
    window_configs["execution"]["policies"]["twap"]["end_of_window"] = "sweep_marketable"
    with open(path, newline="") as handle:
        rows = list(csv.reader(handle))
    rows = [rows[0], *(row for row in rows[1:] if int(row[1]) != end_ns)]
    with open(path, "w", newline="") as handle:
        csv.writer(handle).writerows(rows)

    context = ExecutionHarness(window_configs).run(
        RunRequest(
            policy="twap", style="aggressive", quantity_base=100, n_slices=2, latency_ns=0
        )
    )
    result = context.result
    assert result is not None
    assert context.outcome.n_events == 5
    assert context.validation is not None and context.validation.n_issues == 0
    assert len(result.child_orders) == 1
    assert result.child_orders[0].created_ns == end_ns
    assert result.filled_base == 100
    assert len(result.fills) == 1 and result.fills[0].timestamp_ns == end_ns
    assert result.terminal_mid_ticks == 10000.5
    assert result.metadata["last_observed_ns"] == session_start + 3_000_000_000
