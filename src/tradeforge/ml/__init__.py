"""Machine-learning layer: fill-probability baselines with leak control.

This layer exists to answer "do microstructure features carry signal", not to
produce a trading strategy. Every model is a baseline or a linear model, the
split is purged and time ordered, and a label-shuffle canary runs on every
report.
"""

from .dataset import (
    DatasetReport,
    FillDatasetBuilder,
    Sample,
    SampleConfig,
    feature_matrix,
)
from .evaluate import (
    CalibrationBin,
    ClassificationReport,
    FeatureLeakFinding,
    LeakageCanary,
    canary_tolerance,
    evaluate,
    feature_leak_screen,
    leakage_canary,
    null_auc_standard_error,
    roc_auc,
)
from .models import (
    BaseRateModel,
    FillProbabilityModel,
    ImbalanceThresholdModel,
    LogisticRegressionModel,
    MidModel,
    RidgeModel,
    Standardizer,
    build_models,
)
from .pipeline import MlConfig, MlExperimentResult, run_fill_probability_experiment
from .split import (
    PurgedSplit,
    assert_disjoint,
    assert_time_ordered,
    purged_time_split,
    required_embargo_samples,
)

__all__ = [
    "BaseRateModel",
    "CalibrationBin",
    "ClassificationReport",
    "DatasetReport",
    "FeatureLeakFinding",
    "FillDatasetBuilder",
    "FillProbabilityModel",
    "ImbalanceThresholdModel",
    "LeakageCanary",
    "LogisticRegressionModel",
    "MidModel",
    "MlConfig",
    "MlExperimentResult",
    "PurgedSplit",
    "RidgeModel",
    "Sample",
    "SampleConfig",
    "Standardizer",
    "assert_disjoint",
    "assert_splits_usable",
    "assert_time_ordered",
    "build_models",
    "canary_tolerance",
    "evaluate",
    "feature_leak_screen",
    "feature_matrix",
    "leakage_canary",
    "null_auc_standard_error",
    "purged_time_split",
    "required_embargo_samples",
    "roc_auc",
    "run_fill_probability_experiment",
]
