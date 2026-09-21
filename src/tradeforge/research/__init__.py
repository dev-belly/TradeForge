"""Research layer: pre-registered experiments, honest statistics, reproducibility."""

from .bootstrap import ConfidenceInterval, block_bootstrap_ci, iid_bootstrap_ci
from .compare import (
    PairedComparison,
    apply_holm,
    holm_bonferroni,
    paired_comparison,
    sign_test_p_value,
)
from .experiments import ExperimentCell, ExperimentSpec, build_experiment_specs
from .regimes import RegimeLabel, RegimeTally, RegimeThresholds, classify_state
from .registry import (
    EnvironmentRecord,
    ExperimentRecord,
    ExperimentRegistry,
    git_commit,
    read_rows,
)
from .runner import CellAggregate, ExperimentResult, run_experiment

__all__ = [
    "CellAggregate",
    "ConfidenceInterval",
    "EnvironmentRecord",
    "ExperimentCell",
    "ExperimentRecord",
    "ExperimentRegistry",
    "ExperimentResult",
    "ExperimentSpec",
    "PairedComparison",
    "RegimeLabel",
    "RegimeTally",
    "RegimeThresholds",
    "apply_holm",
    "block_bootstrap_ci",
    "build_experiment_specs",
    "classify_state",
    "git_commit",
    "holm_bonferroni",
    "iid_bootstrap_ci",
    "paired_comparison",
    "read_rows",
    "run_experiment",
    "sign_test_p_value",
]
