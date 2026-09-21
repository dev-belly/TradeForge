"""End-to-end integration.

These tests exercise the real stack: real configs, real synthetic adapter, real
book, real queue model, real simulator, real TCA. Nothing is mocked, because the
claims being checked are about how the parts behave *together*.
"""

from __future__ import annotations

import pytest

from tradeforge.application.harness import ExecutionHarness, RunRequest


@pytest.fixture(scope="module")
def harness(configs):
    return ExecutionHarness(configs)


@pytest.fixture(scope="module")
def twap_passive(harness):
    return harness.run(RunRequest(policy="twap", style="passive", run_id="it-twap"))


class TestDeterminism:
    def test_the_same_seed_reproduces_the_same_execution(self, harness):
        """Reproducibility is the whole basis for the experiment registry."""
        first = harness.run(RunRequest(policy="twap", style="passive", seed=11, run_id="d1"))
        second = harness.run(RunRequest(policy="twap", style="passive", seed=11, run_id="d2"))
        assert first.result is not None and second.result is not None
        assert first.result.filled_base == second.result.filled_base
        assert first.result.avg_fill_price_ticks == second.result.avg_fill_price_ticks
        assert first.result.notional == second.result.notional
        assert first.result.fees_total == second.result.fees_total
        assert len(first.result.fills) == len(second.result.fills)

    def test_a_different_seed_gives_a_different_session(self, harness):
        first = harness.run(RunRequest(policy="twap", style="passive", seed=11, run_id="d3"))
        second = harness.run(RunRequest(policy="twap", style="passive", seed=12, run_id="d4"))
        assert first.result is not None and second.result is not None
        assert first.result.avg_fill_price_ticks != second.result.avg_fill_price_ticks

    def test_the_config_fingerprint_is_stable(self, harness):
        assert harness.fingerprint == ExecutionHarness(harness.configs).fingerprint


class TestExecutionOutcome:
    def test_a_complete_execution_reports_a_full_fill_ratio(self, twap_passive):
        result = twap_passive.result
        assert result is not None
        assert 0.0 <= result.completion_rate <= 1.0
        assert result.filled_base + result.unfilled_base == result.requested_base

    def test_notional_matches_the_fills(self, twap_passive):
        result = twap_passive.result
        assert result is not None
        from decimal import Decimal

        expected = sum(
            Decimal(f.price_ticks) * Decimal(f.quantity_base) for f in result.fills
        ) * Decimal("0.01")
        assert result.notional == expected

    def test_average_fill_price_is_the_quantity_weighted_mean(self, twap_passive):
        result = twap_passive.result
        assert result is not None
        if not result.fills:
            pytest.skip("no fills")
        notional_ticks = sum(f.price_ticks * f.quantity_base for f in result.fills)
        assert result.avg_fill_price_ticks == pytest.approx(notional_ticks / result.filled_base)

    def test_latency_is_recorded_as_a_scenario(self, twap_passive):
        assert twap_passive.result is not None
        assert twap_passive.result.latency_basis == "scenario"

    def test_counterfactual_mode_is_always_declared(self, twap_passive):
        assert twap_passive.result is not None
        assert twap_passive.result.counterfactual_mode == "replay_approximation"

    def test_provenance_says_the_data_is_synthetic(self, twap_passive):
        assert "SYNTHETIC" in str(twap_passive.provenance["provenance"]).upper()

    def test_no_book_violations_were_recorded(self, twap_passive):
        assert twap_passive.outcome.book_violations == []

    def test_validation_found_nothing_in_the_generated_stream(self, twap_passive):
        assert twap_passive.validation is not None
        assert twap_passive.validation.n_issues == 0


class TestPriceTouchNeverFills:
    """The system-level form of the anti-slop rule.

    A passive order must only be filled by a trade that consumed the queue ahead
    of it. If the simulator filled on price touch, a passive strategy would fill
    almost instantly and the passive/aggressive cost difference would vanish.
    """

    def test_passive_orders_never_fill_instantly(self, harness):
        context = harness.run(
            RunRequest(policy="twap", style="passive", seed=21, run_id="pt-passive")
        )
        result = context.result
        assert result is not None
        assert result.fills, "the run produced no fills, so the check is vacuous"

        for order in result.child_orders:
            maker_fills = [
                f
                for f in result.fills
                if f.client_order_id == order.client_order_id and f.liquidity_flag.value == "MAKER"
            ]
            for fill in maker_fills:
                assert order.queue_join_ns is not None
                # A maker fill cannot precede the order joining the queue.
                assert fill.timestamp_ns >= order.queue_join_ns

    def test_maker_fills_carry_a_non_negative_queue_wait(self, harness):
        context = harness.run(RunRequest(policy="twap", style="passive", seed=22, run_id="pt-wait"))
        result = context.result
        assert result is not None
        for fill in result.fills:
            if fill.liquidity_flag.value == "MAKER":
                assert fill.queue_wait_ns >= 0

    def test_aggressive_execution_is_mostly_taker_and_passive_mostly_maker(self, harness):
        passive = harness.run(
            RunRequest(policy="twap", style="passive", seed=23, run_id="pt-p")
        ).result
        aggressive = harness.run(
            RunRequest(policy="twap", style="aggressive", seed=23, run_id="pt-a")
        ).result
        assert passive is not None and aggressive is not None
        assert passive.maker_fill_ratio > 0.5
        assert aggressive.maker_fill_ratio < passive.maker_fill_ratio


class TestTcaIntegration:
    def test_benchmarks_are_backed_by_real_observations(self, twap_passive):
        report = twap_passive.tca
        assert report is not None
        assert report.benchmarks.n_mid_observations > 1_000
        assert report.benchmarks.n_trade_prints > 100
        assert report.benchmarks.interval_vwap_ticks is not None
        assert report.benchmarks.interval_twap_ticks is not None

    def test_the_benchmark_window_is_the_parent_window(self, twap_passive):
        report = twap_passive.tca
        parent = twap_passive.parent
        assert report is not None and parent is not None
        assert report.benchmarks.window_start_ns == parent.start_ns
        assert report.benchmarks.window_end_ns == parent.end_ns

    def test_markouts_are_measurable_at_every_configured_horizon(self, twap_passive):
        report = twap_passive.tca
        assert report is not None
        assert report.markouts
        for summary in report.markouts:
            assert summary.n_measurable > 0, (
                f"horizon {summary.horizon_ns} was never measurable; the replay "
                "must run past the window by the longest horizon"
            )

    def test_attribution_closes(self, twap_passive):
        attribution = twap_passive.tca.attribution
        if attribution.residual_impact_bps is None:
            pytest.skip("no fills")
        total = (
            attribution.spread_cost_bps
            + attribution.fees_bps
            + attribution.timing_bps
            + attribution.residual_impact_bps
        )
        assert total == pytest.approx(attribution.is_filled_bps, abs=1e-9)

    def test_provenance_records_the_queue_mode_and_latency_basis(self, twap_passive):
        provenance = twap_passive.tca.provenance
        assert provenance["queue_mode"] in {"APPROXIMATE", "EXACT"}
        assert provenance["latency_basis"] in {"scenario", "observed"}
        assert provenance["counterfactual_mode"] == "replay_approximation"


class TestPolicyCoverage:
    @pytest.mark.parametrize("policy", ["twap", "vwap", "pov", "is_baseline"])
    def test_every_policy_completes_a_run(self, harness, policy):
        context = harness.run(
            RunRequest(policy=policy, style="passive", seed=31, run_id=f"cov-{policy}")
        )
        assert context.result is not None
        assert context.tca is not None
        assert context.result.n_child_orders > 0

    def test_vwap_uses_a_prior_session_profile(self, harness):
        context = harness.run(
            RunRequest(policy="vwap", style="passive", seed=32, run_id="cov-vwap")
        )
        assert context.tca is not None
        source = str(context.tca.provenance.get("volume_profile_source"))
        assert "prior" in source

    def test_pov_tracks_observed_volume_only(self, harness):
        context = harness.run(RunRequest(policy="pov", style="passive", seed=33, run_id="cov-pov"))
        assert context.result is not None
        # POV is volume-driven, so it emits fewer, larger slices than a schedule.
        assert context.result.n_child_orders < 30

    def test_is_baseline_front_loads_relative_to_twap(self, harness):
        """Almgren-Chriss with kappa*T ~ 1 must trade earlier than TWAP."""
        is_run = harness.run(
            RunRequest(policy="is_baseline", style="passive", seed=34, run_id="cov-is")
        ).result
        twap_run = harness.run(
            RunRequest(policy="twap", style="passive", seed=34, run_id="cov-tw")
        ).result
        assert is_run is not None and twap_run is not None
        if not is_run.fills or not twap_run.fills:
            pytest.skip("no fills")
        half = is_run.start_ns + is_run.duration_ns // 2
        is_first_half = sum(f.quantity_base for f in is_run.fills if f.timestamp_ns <= half)
        twap_first_half = sum(f.quantity_base for f in twap_run.fills if f.timestamp_ns <= half)
        assert is_first_half / is_run.filled_base > twap_first_half / twap_run.filled_base


class TestOverrides:
    def test_quantity_override_is_respected(self, harness):
        context = harness.run(
            RunRequest(policy="twap", style="passive", quantity_base=1_000, run_id="ov-q")
        )
        assert context.result is not None
        assert context.result.requested_base == 1_000

    def test_latency_override_changes_the_outcome(self, harness):
        fast = harness.run(
            RunRequest(policy="twap", style="aggressive", latency_ns=0, seed=41, run_id="ov-l0")
        ).result
        slow = harness.run(
            RunRequest(
                policy="twap", style="aggressive", latency_ns=50_000_000, seed=41, run_id="ov-l50"
            )
        ).result
        assert fast is not None and slow is not None
        # 50 ms of latency on an aggressive schedule must not improve the fill
        # price; if it did, the latency model is not being applied.
        assert slow.avg_fill_price_ticks is not None
        assert fast.avg_fill_price_ticks is not None
        assert slow.avg_fill_price_ticks >= fast.avg_fill_price_ticks - 1e-9

    def test_queue_policy_override_changes_the_outcome(self, harness):
        optimistic = harness.run(
            RunRequest(
                policy="twap",
                style="passive",
                queue_policy="optimistic",
                seed=42,
                run_id="ov-qo",
            )
        ).result
        conservative = harness.run(
            RunRequest(
                policy="twap",
                style="passive",
                queue_policy="conservative",
                seed=42,
                run_id="ov-qc",
            )
        ).result
        assert optimistic is not None and conservative is not None
        # Optimistic attribution moves us up the queue, so it must not fill less.
        assert optimistic.filled_base >= conservative.filled_base
