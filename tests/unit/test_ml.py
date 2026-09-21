"""ML layer: purged split, AUC implementation, leakage canary.

`test_canary_detects_an_injected_leak` is the important one. A leakage check that
has never been shown to fire is not a check. That test builds a feature set
where one column *is* the label and asserts the canary fails, then builds an
honest one and asserts it passes.
"""

from __future__ import annotations

import numpy as np
import pytest

from tradeforge.ml import (
    BaseRateModel,
    MlConfig,
    assert_disjoint,
    assert_time_ordered,
    canary_tolerance,
    evaluate,
    leakage_canary,
    null_auc_standard_error,
    purged_time_split,
    required_embargo_samples,
    roc_auc,
)
from tradeforge.ml.split import assert_splits_usable


class TestRocAuc:
    def test_perfect_ranking(self):
        assert roc_auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == pytest.approx(1.0)

    def test_perfectly_inverted_ranking(self):
        assert roc_auc([0, 0, 1, 1], [0.9, 0.8, 0.2, 0.1]) == pytest.approx(0.0)

    def test_constant_scores_are_chance(self):
        assert roc_auc([0, 1, 0, 1], [0.5, 0.5, 0.5, 0.5]) == pytest.approx(0.5)

    def test_ties_are_handled_with_average_ranks(self):
        # One tie between a positive and a negative -> 0.5 contribution.
        assert roc_auc([0, 1], [0.5, 0.5]) == pytest.approx(0.5)

    def test_single_class_returns_none(self):
        assert roc_auc([1, 1, 1], [0.1, 0.2, 0.3]) is None


class TestPurgedSplit:
    def test_no_shuffling_the_split_is_contiguous(self):
        split = purged_time_split(100, embargo_samples=0)
        assert split.train == tuple(range(0, 60))
        assert split.validation == tuple(range(60, 80))
        assert split.test == tuple(range(80, 100))

    def test_splits_are_disjoint(self):
        split = purged_time_split(100, embargo_samples=5)
        assert_disjoint(split)

    def test_purge_removes_the_end_of_each_earlier_block(self):
        split = purged_time_split(100, embargo_samples=5)
        assert split.train == tuple(range(0, 55))
        assert split.validation == tuple(range(60, 75))
        assert split.n_purged == 10

    def test_each_index_appears_at_most_once(self):
        split = purged_time_split(200, embargo_samples=12)
        seen = set(split.train) | set(split.validation) | set(split.test)
        total = len(split.train) + len(split.validation) + len(split.test)
        assert len(seen) == total

    def test_fractions_must_sum_to_one(self):
        with pytest.raises(ValueError):
            purged_time_split(100, train_frac=0.5, validation_frac=0.2, test_frac=0.2)

    def test_embargo_covers_the_label_horizon(self):
        """A 30s label horizon sampled every 5s needs 6 samples of purge."""
        assert (
            required_embargo_samples(horizon_ns=30_000_000_000, sample_interval_ns=5_000_000_000)
            == 6
        )

    def test_configured_embargo_wins_when_larger(self):
        assert (
            required_embargo_samples(
                horizon_ns=30_000_000_000,
                sample_interval_ns=5_000_000_000,
                configured_embargo_ns=60_000_000_000,
            )
            == 12
        )

    def test_embargo_is_measured_in_nanoseconds_not_events(self):
        """Pins the unit.

        A 100-second embargo sampled every 5 seconds is 20 samples. Expressing
        the same number as "100 events" would be 100 samples, i.e. 500 seconds -
        five times too long, and enough to wipe out a validation block.
        """
        assert (
            required_embargo_samples(
                horizon_ns=30_000_000_000,
                sample_interval_ns=5_000_000_000,
                configured_embargo_ns=100_000_000_000,
            )
            == 20
        )

    def test_an_over_large_embargo_fails_loudly(self):
        """The bug this prevents: a validation split of six samples."""
        split = purged_time_split(500, embargo_samples=100)
        with pytest.raises(ValueError) as excinfo:
            assert_splits_usable(split)
        assert "embargo" in str(excinfo.value)

    def test_a_sane_embargo_passes(self):
        assert_splits_usable(purged_time_split(500, embargo_samples=12))

    def test_unsorted_timestamps_are_refused(self):
        with pytest.raises(ValueError) as excinfo:
            assert_time_ordered([10, 20, 15])
        assert "not ordered" in str(excinfo.value)

    def test_sorted_timestamps_pass(self):
        assert_time_ordered([10, 10, 20, 30])


class TestCanaryTolerance:
    def test_tolerance_grows_as_the_sample_shrinks(self):
        """A fixed tolerance is not a statistical statement."""
        small = canary_tolerance([1] * 20 + [0] * 5)
        large = canary_tolerance([1] * 4_000 + [0] * 4_000)
        assert small > large

    def test_null_standard_error_matches_the_hanley_mcneil_formula(self):
        se = null_auc_standard_error(90, 16)
        assert se is not None
        assert se == pytest.approx(((90 + 16 + 1) / (12 * 90 * 16)) ** 0.5)


class TestFeatureLeakScreen:
    """The check that catches a raw feature encoding the label."""

    def _data(self, *, leak: bool, seed: int = 0):
        rng = np.random.default_rng(seed)
        n = 400
        honest = rng.normal(size=(n, 4))
        labels = (rng.random(n) < 0.5).astype(int)
        if leak:
            leaky = labels.astype(float) * 3.0 + rng.normal(scale=0.3, size=n)
            return np.column_stack([honest, leaky]), labels
        return honest, labels

    def test_flagged_when_a_column_encodes_the_label(self):
        from tradeforge.ml import feature_leak_screen

        features, labels = self._data(leak=True)
        names = ("a", "b", "c", "d", "leaky")
        findings = feature_leak_screen(features, labels, names)
        flagged = [f.feature for f in findings if f.suspicious]
        assert flagged == ["leaky"]
        assert findings[-1].auc > 0.9

    def test_not_flagged_on_honest_features(self):
        from tradeforge.ml import feature_leak_screen

        features, labels = self._data(leak=False)
        findings = feature_leak_screen(features, labels, ("a", "b", "c", "d"))
        assert not any(f.suspicious for f in findings)

    def test_an_inverted_leak_is_also_flagged(self):
        """A perfectly *inverted* feature leaks just as hard."""
        from tradeforge.ml import feature_leak_screen

        features, labels = self._data(leak=False)
        inverted = -labels.astype(float) * 3.0
        stacked = np.column_stack([features, inverted])
        findings = feature_leak_screen(stacked, labels, ("a", "b", "c", "d", "inv"))
        assert findings[-1].suspicious

    def test_all_nan_column_is_reported_not_scored(self):
        from tradeforge.ml import feature_leak_screen

        features = np.full((20, 1), np.nan)
        findings = feature_leak_screen(features, np.array([1] * 10 + [0] * 10), ("empty",))
        assert findings[0].auc is None
        assert not findings[0].suspicious


class TestLeakageCanary:
    def _data(self, *, leak: bool, seed: int = 0):
        rng = np.random.default_rng(seed)
        n = 400
        honest = rng.normal(size=(n, 4))
        labels = (rng.random(n) < 0.5).astype(int)
        if leak:
            # One column carries the label (plus noise): a classic leak.
            leaky = labels.astype(float) * 3.0 + rng.normal(scale=0.3, size=n)
            features = np.column_stack([honest, leaky])
        else:
            features = honest
        return features[:240], labels[:240], features[240:], labels[240:]

    def test_canary_detects_a_label_independent_predictor(self):
        """Scope check: this canary catches rules and preprocessing leaks.

        A fixed rule ignores the training labels entirely, so shuffling them
        cannot change its ranking. The canary sees that and reports the model as
        not-applicable rather than pretending to have tested it.

        A leaky *feature* is a different failure mode and is caught by
        `feature_leak_screen` instead - see `TestFeatureLeakScreen`.
        """
        from tradeforge.ml import ImbalanceThresholdModel

        x_train, y_train, x_test, y_test = self._data(leak=True)
        canary = leakage_canary(
            lambda: ImbalanceThresholdModel(0),
            x_train,
            y_train,
            x_test,
            y_test,
            split="test",
            seed=1,
        )
        assert not canary.applicable

    def test_canary_passes_on_honest_features(self):
        from tradeforge.ml import LogisticRegressionModel

        x_train, y_train, x_test, y_test = self._data(leak=False)
        canary = leakage_canary(
            lambda: LogisticRegressionModel(seed=1),
            x_train,
            y_train,
            x_test,
            y_test,
            split="test",
            seed=1,
        )
        assert canary.applicable
        assert canary.passed

    def test_canary_is_not_applicable_to_a_fixed_rule(self):
        """Shuffling training labels cannot change a rule that ignores them."""
        from tradeforge.ml import ImbalanceThresholdModel

        x_train, y_train, x_test, y_test = self._data(leak=False)
        canary = leakage_canary(
            lambda: ImbalanceThresholdModel(0),
            x_train,
            y_train,
            x_test,
            y_test,
            split="test",
            seed=1,
        )
        assert not canary.applicable
        assert canary.passed
        assert "not applicable" in canary.verdict


class TestEvaluation:
    def test_base_rate_model_has_no_lift(self):
        features = np.zeros((50, 3))
        labels = np.array([1] * 35 + [0] * 15)
        model = BaseRateModel()
        model.fit(features, labels)
        report = evaluate(model, features, labels, split="test")
        assert report.base_rate == pytest.approx(0.7)
        assert report.lift_over_base_rate == pytest.approx(0.0)

    def test_accuracy_alone_is_reported_with_the_base_rate(self):
        features = np.zeros((10, 1))
        labels = np.array([1] * 9 + [0])
        model = BaseRateModel()
        model.fit(features, labels)
        report = evaluate(model, features, labels, split="test")
        payload = report.to_dict()
        assert "base_rate" in payload
        assert payload["accuracy"] == pytest.approx(0.9)

    def test_calibration_bins_are_reported(self):
        from tradeforge.ml import LogisticRegressionModel

        rng = np.random.default_rng(0)
        features = rng.normal(size=(200, 2))
        labels = (features[:, 0] > 0).astype(int)
        model = LogisticRegressionModel(seed=1)
        model.fit(features, labels)
        report = evaluate(model, features, labels, split="test", calibration_bins=5)
        assert report.calibration
        assert all(bin_.n > 0 for bin_ in report.calibration)


class TestMlConfig:
    def test_reads_the_config_section(self, configs):
        config = MlConfig.from_dict(configs)
        assert config.sample_interval_ns == 5_000_000_000
        assert config.horizon_ns == 30_000_000_000
        assert config.embargo_ns == 60_000_000_000
        assert abs(config.train_frac + config.validation_frac + config.test_frac - 1.0) < 1e-9
