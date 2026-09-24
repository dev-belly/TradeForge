"""Commands that run the pre-registered experiments and the ML baselines."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from ...application.harness import ExecutionHarness
from ...data.registry import create_adapter
from ...infrastructure.logging import configure_logging
from ...ml import (
    FillDatasetBuilder,
    MlConfig,
    SampleConfig,
    run_fill_probability_experiment,
)
from ...research import ExperimentRegistry, build_experiment_specs, run_experiment
from ..console import caveat_block, render_mapping, render_table
from .shared import DEFAULT_CONFIG_DIR, ConfigDir, harness_for, json_dumps

__all__ = ["fill_probability", "list_experiments", "register", "run_experiments"]

research_app = typer.Typer(help="Run pre-registered experiments.", no_args_is_help=True)
ml_app = typer.Typer(help="Fit and evaluate fill-probability baselines.", no_args_is_help=True)


@research_app.command("list")
def list_experiments(configs_dir: ConfigDir = DEFAULT_CONFIG_DIR) -> None:
    """List the pre-registered experiment grids."""
    specs = build_experiment_specs(harness_for(configs_dir).configs)
    rows = [
        [spec.name, len(spec.cells), spec.baseline or "-", spec.description]
        for spec in specs.values()
    ]
    typer.echo(render_table(["experiment", "cells", "baseline", "description"], rows))


@research_app.command("run")
def run_experiments(
    experiment: Annotated[str, typer.Option("--experiment", help="Grid name, or 'all'.")] = "all",
    seeds: Annotated[int, typer.Option("--seeds", help="Number of sessions per cell.")] = 3,
    n_resamples: Annotated[int, typer.Option("--resamples")] = 2000,
    output: Annotated[Path, typer.Option("--output")] = Path("artifacts/research"),
    configs_dir: ConfigDir = DEFAULT_CONFIG_DIR,
) -> None:
    """Run experiment grids and record every result, including the losses."""
    configure_logging(level="WARNING")
    configs = harness_for(configs_dir).configs
    harness = ExecutionHarness(configs)
    specs = build_experiment_specs(configs)
    base_seed = int(configs["market_data"]["source"]["options"].get("seed", 20260908))
    seed_list = [base_seed + i for i in range(seeds)]

    selected = list(specs.values()) if experiment == "all" else [specs[experiment]]
    registry = ExperimentRegistry(output)
    records = []
    result = None
    for spec in selected:
        result = run_experiment(harness, spec, seeds=seed_list, n_resamples=n_resamples)
        records.append(registry.record(result))
        typer.echo(f"\n{spec.name} - {spec.description}")
        typer.echo(
            render_table(
                ["cell", "n", "mean", "ci_low", "ci_high", "fill", "participation", "maker"],
                [
                    [
                        c.label,
                        c.n_usable,
                        c.ci.estimate,
                        c.ci.lower,
                        c.ci.upper,
                        f"{c.mean_fill_ratio:.2%}",
                        f"{c.mean_participation_rate:.3%}",
                        f"{c.mean_maker_ratio:.1%}",
                    ]
                    for c in result.cells
                ],
            )
        )
        if result.comparisons:
            typer.echo("")
            typer.echo(
                render_table(
                    ["cell", "vs", "diff_bps", "ci_low", "ci_high", "p", "holm_p", "sig"],
                    [
                        [
                            c.label_a,
                            c.label_b,
                            c.mean_difference,
                            c.ci.lower,
                            c.ci.upper,
                            c.sign_test_p,
                            c.holm_adjusted_p,
                            c.significant,
                        ]
                        for c in result.comparisons
                    ],
                )
            )
    index = registry.write_index(records)
    typer.echo("")
    typer.echo(f"artefacts: {registry.output_dir}")
    typer.echo(f"index:     {index}")
    if result is not None:
        typer.echo("")
        typer.echo(caveat_block(result.caveats))


@ml_app.command("fill-probability")
def fill_probability(
    seed: Annotated[int | None, typer.Option("--seed")] = None,
    configs_dir: ConfigDir = DEFAULT_CONFIG_DIR,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Fit the fill-probability baselines on a purged time split."""
    configure_logging(level="WARNING")
    configs = harness_for(configs_dir).configs
    ml_config = MlConfig.from_dict(configs)
    options = dict(configs["market_data"]["source"].get("options", {}))
    if seed is not None:
        options["seed"] = seed
    source = create_adapter(configs["market_data"]["source"]["adapter"], options)

    builder = FillDatasetBuilder(
        symbol=configs["market_data"]["instrument"]["symbol"],
        config=SampleConfig(
            sample_interval_ns=ml_config.sample_interval_ns,
            horizon_ns=ml_config.horizon_ns,
        ),
    )
    builder.run(source.events())
    result = run_fill_probability_experiment(
        builder.samples, config=ml_config, dataset_report=builder.report.to_dict()
    )
    if json_output:
        typer.echo(json_dumps(result.to_dict()))
        return

    typer.echo("Dataset")
    typer.echo(render_mapping(builder.report.to_dict()))
    typer.echo("")
    typer.echo("Split (time ordered, purged, never shuffled)")
    typer.echo(render_mapping(result.split.to_dict()))
    typer.echo("")
    typer.echo("Validation split")
    typer.echo(_report_table(result.validation_reports))
    typer.echo("")
    typer.echo("Test split")
    typer.echo(_report_table(result.test_reports))
    typer.echo("")
    typer.echo("Leakage canary (shuffled training labels; expect AUC ~ 0.5)")
    typer.echo(
        render_table(
            ["model", "auc_shuffled", "tolerance", "applicable", "passed"],
            [
                [c.model, c.auc_on_shuffled_labels, c.tolerance, c.applicable, c.passed]
                for c in result.canaries
            ],
        )
    )
    typer.echo("")
    typer.echo("Feature leak screen (univariate AUC per raw column)")
    typer.echo(
        render_table(
            ["feature", "auc", "flagged"],
            [[f.feature, f.auc, f.suspicious] for f in result.feature_screen],
        )
    )
    typer.echo("")
    typer.echo(caveat_block(result.notes))


def _report_table(reports: Any) -> str:
    """Per-model metrics, with the lift as an interval rather than a point.

    The point estimate alone invites the reader to treat a fraction of a
    percentage point as a finding. The `excludes_0` column is the answer to the
    question actually being asked: is this model's edge over the base rate
    distinguishable from sampling noise?
    """
    return render_table(
        [
            "model",
            "n",
            "base_rate",
            "accuracy",
            "auc",
            "brier",
            "lift",
            "lift_lo",
            "lift_hi",
            "excludes_0",
        ],
        [
            [
                r.model,
                r.n,
                r.base_rate,
                r.accuracy,
                r.auc,
                r.brier,
                r.lift_over_base_rate,
                r.lift_ci_lower,
                r.lift_ci_upper,
                r.lift_is_distinguishable,
            ]
            for r in reports
        ],
    )


def register(app: typer.Typer) -> None:
    app.add_typer(research_app, name="research")
    app.add_typer(ml_app, name="ml")
