"""Research statistics: bootstrap, paired comparison, correction, regimes."""

from __future__ import annotations

import random

import pytest

from tradeforge.research import (
    RegimeLabel,
    RegimeTally,
    RegimeThresholds,
    apply_holm,
    block_bootstrap_ci,
    classify_state,
    holm_bonferroni,
    iid_bootstrap_ci,
    paired_comparison,
    sign_test_p_value,
)
from tradeforge.research.bootstrap import ConfidenceInterval


class TestBlockBootstrap:
    def test_estimate_is_the_sample_statistic(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        ci = block_bootstrap_ci(values, n_resamples=200, seed=1)
        assert ci.estimate == pytest.approx(3.0)
        assert ci.lower <= ci.estimate <= ci.upper

    def test_is_deterministic_for_a_given_seed(self):
        values = [float(i % 7) for i in range(200)]
        first = block_bootstrap_ci(values, n_resamples=300, seed=99)
        second = block_bootstrap_ci(values, n_resamples=300, seed=99)
        assert first == second

    def test_different_seeds_move_the_interval_slightly(self):
        values = [float(i % 11) for i in range(300)]
        first = block_bootstrap_ci(values, n_resamples=300, seed=1)
        second = block_bootstrap_ci(values, n_resamples=300, seed=2)
        assert first.lower != second.lower

    def test_block_bootstrap_is_wider_on_autocorrelated_data(self):
        """The reason i.i.d. resampling is not used here.

        A strongly autocorrelated series carries less independent information
        than its length suggests. The block bootstrap sees that; the naive
        bootstrap does not, and reports a falsely narrow interval.
        """
        rng = random.Random(0)
        series: list[float] = []
        level = 0.0
        for _ in range(2_000):
            level = 0.95 * level + rng.gauss(0, 1.0)  # AR(1)
            series.append(level)

        block = block_bootstrap_ci(series, block_length=50, n_resamples=400, seed=5)
        iid = iid_bootstrap_ci(series, n_resamples=400, seed=5)
        assert block.width > iid.width, (
            "the block bootstrap should be wider than the i.i.d. bootstrap on "
            "autocorrelated data; if it is not, the dependence is being ignored"
        )

    def test_empty_sample_is_refused(self):
        with pytest.raises(ValueError):
            block_bootstrap_ci([])

    def test_single_observation_is_degenerate_not_fabricated(self):
        ci = block_bootstrap_ci([3.0], n_resamples=100, seed=1)
        assert ci.lower == ci.upper == 3.0
        assert ci.n_resamples == 0
        assert "degenerate" in ci.method

    def test_excludes_zero_is_reported(self):
        ci = ConfidenceInterval(
            estimate=1.0,
            lower=0.5,
            upper=2.0,
            confidence=0.95,
            n_observations=10,
            n_resamples=100,
            method="test",
        )
        assert ci.excludes_zero
        straddling = ConfidenceInterval(
            estimate=0.0,
            lower=-1.0,
            upper=1.0,
            confidence=0.95,
            n_observations=10,
            n_resamples=100,
            method="test",
        )
        assert not straddling.excludes_zero


class TestPairedComparison:
    def test_difference_is_a_minus_b(self):
        a = [1.0, 2.0, 3.0]
        b = [2.0, 3.0, 4.0]
        comparison = paired_comparison("a", "b", a, b, metric="x", n_resamples=100)
        assert comparison.mean_difference == pytest.approx(-1.0)

    def test_unequal_lengths_are_refused(self):
        with pytest.raises(ValueError) as excinfo:
            paired_comparison("a", "b", [1.0, 2.0], [1.0], metric="x")
        assert "Pair by seed" in str(excinfo.value)

    def test_wins_are_counted_on_the_sign_of_the_difference(self):
        a = [1.0, 5.0, 1.0, 1.0]
        b = [2.0, 1.0, 2.0, 2.0]
        comparison = paired_comparison("a", "b", a, b, metric="x", n_resamples=50)
        assert comparison.wins_a == 3
        assert comparison.wins_b == 1

    def test_a_consistent_difference_produces_a_significant_result(self):
        a = [1.0] * 30
        b = [2.0] * 30
        comparison = paired_comparison("a", "b", a, b, metric="x", n_resamples=200)
        assert comparison.mean_difference == pytest.approx(-1.0)
        assert comparison.significant

    def test_noise_does_not_produce_significance(self):
        """A difference that is pure noise must not be reported as an effect."""
        rng = random.Random(3)
        a = [rng.gauss(0, 1) for _ in range(40)]
        b = [rng.gauss(0, 1) for _ in range(40)]
        comparison = paired_comparison("a", "b", a, b, metric="x", n_resamples=400)
        assert not comparison.significant or abs(comparison.mean_difference) < 1.0


class TestSignTest:
    def test_all_wins_gives_a_small_p_value(self):
        assert sign_test_p_value(20, 0) < 1e-5

    def test_balanced_splits_give_a_large_p_value(self):
        assert sign_test_p_value(5, 5) == pytest.approx(1.0)

    def test_no_pairs_is_not_significant(self):
        assert sign_test_p_value(0, 0) == 1.0


class TestHolmCorrection:
    def test_monotone_and_bounded(self):
        adjusted = holm_bonferroni([0.001, 0.02, 0.5])
        assert all(0.0 <= value <= 1.0 for value in adjusted)
        assert adjusted[0] <= adjusted[1] <= adjusted[2]

    def test_smallest_p_is_multiplied_by_the_family_size(self):
        adjusted = holm_bonferroni([0.01, 0.2, 0.4, 0.6])
        assert adjusted[0] == pytest.approx(0.04)

    def test_never_makes_a_p_value_smaller(self):
        raw = [0.03, 0.04, 0.049]
        adjusted = holm_bonferroni(raw)
        assert all(a >= r for a, r in zip(adjusted, raw, strict=True))

    def test_apply_holm_fills_in_the_comparison_objects(self):
        from tradeforge.research import paired_comparison as compare

        comparisons = [
            compare(f"c{i}", "base", [float(i)] * 10, [0.0] * 10, metric="x", n_resamples=50)
            for i in range(1, 4)
        ]
        adjusted = apply_holm(comparisons)
        assert all(c.holm_adjusted_p is not None for c in adjusted)
        assert [c.holm_adjusted_p for c in adjusted] == sorted(c.holm_adjusted_p for c in adjusted)

    def test_empty_family(self):
        assert holm_bonferroni([]) == []


class TestRegimes:
    def _state(self, bid, ask, bid_qty, ask_qty):
        from tradeforge.domain.book import MarketState, PriceLevel

        return MarketState(
            symbol="TEST",
            timestamp_ns=0,
            sequence_id=0,
            best_bid_ticks=bid,
            best_ask_ticks=ask,
            best_bid_qty_base=bid_qty,
            best_ask_qty_base=ask_qty,
            bid_levels=(PriceLevel(bid, bid_qty),),
            ask_levels=(PriceLevel(ask, ask_qty),),
            last_trade_ticks=None,
            last_trade_qty_base=0,
            aggressor_side_known=False,
        )

    def test_thresholds_come_from_config_not_from_the_sample(self):
        thresholds = RegimeThresholds.from_dict(
            {"regimes": {"spread_tight_max_ticks": 2, "spread_wide_min_ticks": 5}}
        )
        assert thresholds.spread_tight_max_ticks == 2
        assert thresholds.spread_wide_min_ticks == 5

    def test_tight_and_wide_are_mutually_exclusive_by_construction(self):
        thresholds = RegimeThresholds()
        tight = classify_state(self._state(9_999, 10_000, 500, 500), thresholds)
        assert RegimeLabel.TIGHT_SPREAD in tight
        assert RegimeLabel.WIDE_SPREAD not in tight

        wide = classify_state(self._state(9_990, 10_010, 500, 500), thresholds)
        assert RegimeLabel.WIDE_SPREAD in wide
        assert RegimeLabel.TIGHT_SPREAD not in wide

    def test_imbalance_uses_the_whole_tracked_depth(self):
        thresholds = RegimeThresholds()
        imbalanced = classify_state(self._state(9_999, 10_001, 5_000, 100), thresholds)
        assert RegimeLabel.IMBALANCED_BOOK in imbalanced

        balanced = classify_state(self._state(9_999, 10_001, 500, 500), thresholds)
        assert RegimeLabel.BALANCED_BOOK in balanced

    def test_labels_overlap_rather_than_excluding_each_other(self):
        """A state can be tight-spread AND volatile AND deep at once."""
        thresholds = RegimeThresholds()
        labels = classify_state(
            self._state(10_000, 10_001, 5_000, 500), thresholds, realized_vol_bps=20.0
        )
        assert RegimeLabel.TIGHT_SPREAD in labels
        assert RegimeLabel.HIGH_VOLATILITY in labels
        assert RegimeLabel.HIGH_DEPTH in labels
        assert len(labels) >= 3

    def test_tally_counts_and_times_each_label(self):
        thresholds = RegimeThresholds()
        tally = RegimeTally()
        state = self._state(9_999, 10_001, 5_000, 100)
        for step in range(10):
            tally.record(classify_state(state, thresholds), step * 1_000)
        assert tally.n_states == 10
        assert tally.counts["imbalanced_book"] == 10
        assert tally.share(RegimeLabel.IMBALANCED_BOOK) == 1.0
        assert tally.to_dict()["duration_s"]["imbalanced_book"] > 0
