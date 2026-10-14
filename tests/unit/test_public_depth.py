from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from tradeforge.application.public_depth import load_depth_capture, run_public_depth

CAPTURE = Path(__file__).parents[2] / "data/examples/public-depth-20261013.json.gz"


def test_public_episode_is_continuous_and_matching_outputs_reproduce(tmp_path):
    source, states = load_depth_capture(CAPTURE)
    assert source["data_kind"] == "public_market_capture"
    assert len(states) > 100
    assert all(not state.is_crossed for state in states)
    first = run_public_depth(CAPTURE, tmp_path / "first")
    second = run_public_depth(CAPTURE, tmp_path / "second")
    assert first == second
    assert len(first["rows"]) == 18
    assert all(0 <= row["fill_ratio"] <= 1 for row in first["rows"])
    for row in first["rows"]:
        if row["filled_base"]:
            reference_notional = (
                first["reference_mid_ticks"] * float(first["tick_size"]) * row["filled_base"]
            )
            actual = (
                (row["executed_notional_quote"] + row["fees_quote"] - reference_notional)
                / reference_notional
                * 10000
            )
            assert row["executed_cost_bps"] == pytest.approx(actual, abs=1e-8)


def test_missing_depth_packet_cannot_be_silently_repaired(tmp_path):
    with gzip.open(CAPTURE, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    # Remove a packet after the snapshot bridge; later U must expose the gap.
    events = payload["depth_events"]
    active = [
        i for i, event in enumerate(events) if event["u"] > payload["snapshot"]["lastUpdateId"]
    ]
    del events[active[len(active) // 2]]
    path = tmp_path / "gap.json.gz"
    path.write_bytes(gzip.compress(json.dumps(payload).encode(), mtime=0))
    with pytest.raises(ValueError, match="sequence gap"):
        load_depth_capture(path)


def test_fractional_asset_units_are_rejected_instead_of_rounded(tmp_path):
    with gzip.open(CAPTURE, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    payload["quantity_step"] = "0.00001"
    path = tmp_path / "fractional.json.gz"
    path.write_bytes(gzip.compress(json.dumps(payload).encode(), mtime=0))
    with pytest.raises(ValueError, match="integral base-asset"):
        load_depth_capture(path)
