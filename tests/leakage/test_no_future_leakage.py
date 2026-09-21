"""Future-leakage tests.

These are the tests that justify the claim that a policy cannot see the future.
They are behavioural, not structural: they run the same computation on a
truncated event stream and assert the past is unchanged.

The strongest form of the check is *prefix invariance*: for any time t, the
feature vector and the market state computed while streaming the full session
must equal the one computed while streaming only the events up to t. If any
window, statistic or cache looked forward, the two would differ.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from tradeforge.data.registry import create_adapter
from tradeforge.domain.enums import DataType
from tradeforge.orderbook import BookSettings, create_book


def _stream(events, engine, book, depth=10):
    """Feed events, returning the state after each one."""
    states = []
    for event in events:
        book.apply(event)
        snapshot = book.snapshot(depth)
        engine.on_event(event, snapshot)
        states.append((snapshot, engine.to_market_state(snapshot)))
    return states


@pytest.fixture
def events():
    adapter = create_adapter(
        "synthetic", {"seed": 4242, "n_events": 3_000, "start_time_ns": 34_200_000_000_000}
    )
    return list(adapter.events())


def _fresh():
    from tradeforge.microstructure import MicrostructureEngine as Engine

    return (
        Engine(),
        create_book(
            symbol="SYNTH",
            settings=BookSettings.from_dict({}),
            data_type=DataType.L2_MBP,
        ),
    )


class TestPrefixInvariance:
    def test_market_state_at_t_is_identical_on_a_truncated_stream(self, events):
        cutoff = 1_800
        engine, book = _fresh()
        full = _stream(events, engine, book)

        engine2, book2 = _fresh()
        truncated = _stream(events[:cutoff], engine2, book2)

        assert len(truncated) == cutoff
        for index in range(cutoff):
            expected_snapshot, expected_state = full[index]
            actual_snapshot, actual_state = truncated[index]
            assert actual_snapshot == expected_snapshot
            assert actual_state == expected_state

    def test_feature_vector_at_t_is_identical_on_a_truncated_stream(self, events):
        cutoff = 1_500
        engine, book = _fresh()
        captured: list = []
        for index, event in enumerate(events):
            book.apply(event)
            snapshot = book.snapshot(10)
            engine.on_event(event, snapshot)
            if index < cutoff:
                captured.append(engine.observe(snapshot))

        engine2, book2 = _fresh()
        for index, event in enumerate(events[:cutoff]):
            book2.apply(event)
            snapshot = book2.snapshot(10)
            engine2.on_event(event, snapshot)
            assert engine2.observe(snapshot) == captured[index]

    def test_cumulative_volume_never_decreases(self, events):
        engine, book = _fresh()
        previous = 0
        for event in events:
            book.apply(event)
            snapshot = book.snapshot(10)
            engine.on_event(event, snapshot)
            state = engine.to_market_state(snapshot)
            assert state.market_volume_base >= previous
            previous = state.market_volume_base

    def test_rolling_volume_stays_below_cumulative(self, events):
        """Pins the distinction between the two volume measures.

        Conflating them is a real bug that was found by exactly this check: POV
        differences consecutive readings to get interval volume, and a rolling
        value makes that difference meaningless.
        """
        engine, book = _fresh()
        for event in events:
            book.apply(event)
            snapshot = book.snapshot(10)
            engine.on_event(event, snapshot)
            state = engine.to_market_state(snapshot)
            assert state.recent_volume_base <= state.market_volume_base


class TestPolicyIsolation:
    def test_market_state_exposes_no_event_stream_handle(self, events):
        engine, book = _fresh()
        for event in events[:500]:
            book.apply(event)
            snapshot = book.snapshot(10)
            engine.on_event(event, snapshot)
        state = engine.to_market_state(book.snapshot(10))

        field_names = set(type(state).__dataclass_fields__)
        forbidden = {"events", "stream", "adapter", "book", "engine", "future", "next"}
        assert not (field_names & forbidden), (
            "MarketState exposes a handle that would let a policy look ahead: "
            f"{sorted(field_names & forbidden)}"
        )

    def test_market_state_is_frozen(self, events):
        engine, book = _fresh()
        for event in events[:200]:
            book.apply(event)
            snapshot = book.snapshot(10)
            engine.on_event(event, snapshot)
        state = engine.to_market_state(book.snapshot(10))
        with pytest.raises(FrozenInstanceError):
            state.best_bid_ticks = 1

    def test_snapshot_handed_to_a_policy_is_immutable(self, events):
        engine, book = _fresh()
        for event in events[:200]:
            book.apply(event)
            snapshot = book.snapshot(10)
            engine.on_event(event, snapshot)
        state = engine.to_market_state(book.snapshot(10))
        with pytest.raises(FrozenInstanceError):
            state.bid_levels[0].quantity_base = 0


class TestVwapProfileLeakage:
    def test_current_session_profile_is_refused(self):
        """Scheduling from the same session's realized curve is leakage."""
        from tradeforge.domain.exceptions import LeakageError
        from tradeforge.execution.volume_profile import merge_profiles

        with pytest.raises(LeakageError) as excinfo:
            merge_profiles([], source="current-session")
        assert "prior-session" in str(excinfo.value)

    def test_vwap_policy_requires_a_profile(self, parent_order, instrument):
        from tradeforge.domain.exceptions import LeakageError
        from tradeforge.execution.oms import Oms
        from tradeforge.execution.policies import VwapPolicy

        with pytest.raises(LeakageError):
            VwapPolicy(
                parent=parent_order,
                spec=instrument,
                oms=Oms(),
                n_slices=10,
                profile=None,
            )

    def test_prior_session_profile_is_accepted(self, parent_order, instrument):
        from tradeforge.execution.oms import Oms
        from tradeforge.execution.policies import VwapPolicy
        from tradeforge.execution.volume_profile import flat_profile

        policy = VwapPolicy(
            parent=parent_order,
            spec=instrument,
            oms=Oms(),
            n_slices=10,
            profile=flat_profile(10, source="test-prior-session"),
        )
        assert policy.schedule.cumulative_targets[-1] == parent_order.quantity_base

    def test_harness_builds_the_profile_from_other_seeds(self, configs):
        from tradeforge.application.harness import ExecutionHarness, RunRequest

        harness = ExecutionHarness(configs)
        parent = harness.parent_order(RunRequest(policy="vwap"))
        profile = harness.historical_profile(parent, 10)
        assert "prior" in profile.source
        assert profile.n_sessions >= 1
        assert abs(sum(profile.weights) - 1.0) < 1e-6
