"""Pre-registered experiment grids.

The grids live in `configs/research.yaml` and are turned into explicit cells
here. Nothing in this module searches: the cells are enumerated up front, all of
them are run, and all of them are reported - including the ones that fail to
beat the baseline. Discarding a cell because its result is unflattering is the
most common way a backtest lies, so the report keeps a row per run and the
aggregation never filters.

Experiments:

  A  algorithm comparison      twap / vwap / pov / is_baseline
  B  placement style           passive / aggressive / adaptive
  C  latency sensitivity       scenario grid, 0 to 10 ms
  D  participation sensitivity parent size relative to ADV
  E  queue sensitivity         optimistic / neutral / conservative
  F  regime conditioning       cost split by market regime
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..application.harness import RunRequest
from ..domain.exceptions import ConfigurationError

DEFAULT_METRIC = "implementation_shortfall_bps"

EXPERIMENT_CAVEATS: tuple[str, ...] = (
    "All runs use SYNTHETIC data. Numbers describe the simulator's behaviour on "
    "a deterministic generator, not any real venue.",
    "Replay is counterfactual: our orders did not alter the event stream.",
    "Every cell in a grid is reported, including the ones that lose.",
)


@dataclass(frozen=True, slots=True)
class ExperimentCell:
    """One point in the grid: a label plus the run it configures."""

    label: str
    request: RunRequest
    tags: dict[str, str] = field(default_factory=dict)

    def with_seed(self, seed: int) -> ExperimentCell:
        from dataclasses import replace

        return replace(
            self, request=replace(self.request, seed=seed, run_id=f"{self.label}-s{seed}")
        )


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    name: str
    description: str
    metric: str
    cells: tuple[ExperimentCell, ...]
    baseline: str | None = None

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(cell.label for cell in self.cells)


# --------------------------------------------------------------------- builders


def _cell(label: str, **tags: str) -> ExperimentCell:
    return ExperimentCell(label=label, request=RunRequest(policy=label), tags=tags)


def _request(**kwargs: Any) -> RunRequest:
    return RunRequest(**kwargs)


def build_experiment_specs(configs: dict[str, Any]) -> dict[str, ExperimentSpec]:
    """Turn `configs/research.yaml` into concrete grids."""
    research = configs.get("research", {})
    if not isinstance(research, dict):
        raise ConfigurationError("research config must be a mapping")
    declared = research.get("experiments")
    if not isinstance(declared, list) or not declared:
        raise ConfigurationError("research config declares no experiments")

    builders = {
        "A_algorithm_comparison": _algorithm_grid,
        "B_passive_vs_aggressive": _style_grid,
        "C_latency_sensitivity": _latency_grid,
        "D_participation_sensitivity": _participation_grid,
        "E_queue_sensitivity": _queue_grid,
    }
    specs: dict[str, ExperimentSpec] = {}
    for entry in declared:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", ""))
        if name not in builders:
            # F_regime is produced by the regime report, not by a cost grid.
            continue
        specs[name] = builders[name](entry)
    return specs


def _algorithm_grid(entry: dict[str, Any]) -> ExperimentSpec:
    algorithms = [str(a) for a in entry.get("algorithms", ["twap", "vwap", "pov", "is_baseline"])]
    cells = tuple(
        ExperimentCell(
            label=name,
            request=_request(policy=name, style="passive", run_id=name),
            tags={"algorithm": name, "style": "passive"},
        )
        for name in algorithms
    )
    return ExperimentSpec(
        name="A_algorithm_comparison",
        description="Same parent order, same market, four scheduling algorithms.",
        metric=DEFAULT_METRIC,
        cells=cells,
        baseline="twap",
    )


def _style_grid(entry: dict[str, Any]) -> ExperimentSpec:
    styles = [str(s) for s in entry.get("styles", ["passive", "aggressive"])]
    cells = tuple(
        ExperimentCell(
            label=f"twap_{style}",
            request=_request(policy="twap", style=style, run_id=f"twap_{style}"),
            tags={"algorithm": "twap", "style": style},
        )
        for style in styles
    )
    return ExperimentSpec(
        name="B_passive_vs_aggressive",
        description="Same schedule, different liquidity-seeking behaviour.",
        metric=DEFAULT_METRIC,
        cells=cells,
        baseline="twap_aggressive",
    )


def _latency_grid(entry: dict[str, Any]) -> ExperimentSpec:
    grid = entry.get("grid_ns")
    if not isinstance(grid, list) or not grid:
        raise ConfigurationError("C_latency_sensitivity needs a non-empty grid_ns")
    cells = tuple(
        ExperimentCell(
            label=f"latency_{int(str(value))}ns",
            request=_request(
                policy="twap",
                style="aggressive",
                latency_ns=int(str(value)),
                run_id=f"lat_{int(str(value))}",
            ),
            tags={"latency_ns": str(int(str(value)))},
        )
        for value in grid
    )
    return ExperimentSpec(
        name="C_latency_sensitivity",
        description=(
            "Aggressive TWAP across a latency scenario grid. Aggressive placement "
            "is used because latency only bites when the order must cross."
        ),
        metric=DEFAULT_METRIC,
        cells=cells,
        baseline="latency_0ns",
    )


def _participation_grid(entry: dict[str, Any]) -> ExperimentSpec:
    rates = entry.get("rates")
    if not isinstance(rates, list) or not rates:
        raise ConfigurationError("D_participation_sensitivity needs a non-empty rates list")
    cells = tuple(
        ExperimentCell(
            label=f"participation_{rate}",
            request=_request(
                policy="pov",
                style="passive",
                pov_target_rate=float(str(rate)),
                run_id=f"pov_{rate}",
            ),
            tags={"target_participation_rate": str(rate)},
        )
        for rate in rates
    )
    return ExperimentSpec(
        name="D_participation_sensitivity",
        description=(
            "POV target participation. The rate is varied through the policy "
            "config; the resulting *realised* participation is an output, and "
            "the gap between the two is the interesting part."
        ),
        metric=DEFAULT_METRIC,
        cells=cells,
        baseline="participation_0.001",
    )


def _queue_grid(entry: dict[str, Any]) -> ExperimentSpec:
    policies = [str(p) for p in entry.get("policies", ["optimistic", "neutral", "conservative"])]
    cells = tuple(
        ExperimentCell(
            label=f"queue_{policy}",
            request=_request(
                policy="twap",
                style="passive",
                queue_policy=policy,
                run_id=f"queue_{policy}",
            ),
            tags={"cancel_ahead_policy": policy},
        )
        for policy in policies
    )
    return ExperimentSpec(
        name="E_queue_sensitivity",
        description=(
            "L2 queue-attribution assumption. This is the honest way to report "
            "an unobservable: show how much the answer moves when the assumption "
            "moves, instead of picking one and calling it exact."
        ),
        metric=DEFAULT_METRIC,
        cells=cells,
        baseline="queue_neutral",
    )
