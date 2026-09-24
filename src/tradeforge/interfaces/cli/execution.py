"""Execution-facing commands: version, doctor, run, demo, report.

Every command that prints a cost number also prints the provenance that makes it
interpretable. There is deliberately no `--quiet` flag that hides the queue mode,
the latency basis or the counterfactual mode, because those are not decoration -
they change what the number means.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from ...application.harness import ExecutionHarness, RunRequest
from ...infrastructure.logging import configure_logging
from ..console import caveat_block, provenance_block, render_mapping, render_table
from ..reports import HtmlReportBuilder
from .shared import (
    DEFAULT_CONFIG_DIR,
    ConfigDir,
    attribution_rows,
    benchmark_rows,
    fail,
    harness_for,
    json_dumps,
)

__all__ = ["demo", "doctor", "register", "report", "run", "version"]


def version() -> None:
    """Print the package version."""
    from ... import __version__

    typer.echo(f"tradeforge {__version__}")


def doctor(configs_dir: ConfigDir = DEFAULT_CONFIG_DIR) -> None:
    """Check that the environment and configuration are usable."""
    typer.echo("Configuration")
    try:
        configs = harness_for(configs_dir).configs
    except Exception as exc:
        fail(f"  config error: {exc}")
        return
    for name in sorted(configs):
        typer.echo(f"  {name}: ok")

    typer.echo("\nOptional dependencies")
    for module, purpose in (
        ("pyarrow", "Parquet output"),
        ("duckdb", "SQL layer"),
        ("sklearn", "ML baselines"),
        ("streamlit", "dashboard"),
        ("fastapi", "HTTP API"),
    ):
        try:
            __import__(module)
            typer.echo(f"  {module}: available ({purpose})")
        except ImportError:
            typer.echo(f"  {module}: MISSING ({purpose} unavailable)")

    typer.echo("\nC++ core")
    from ...infrastructure.cpp_bridge import core_status

    status = core_status()
    typer.echo(f"  {status['detail']}")
    typer.echo(f"  backend: {status['backend']}")


def run(
    policy: Annotated[str, typer.Option("--policy", help="twap|vwap|pov|is_baseline")] = "twap",
    style: Annotated[str, typer.Option("--style", help="passive|aggressive|adaptive")] = "passive",
    seed: Annotated[int | None, typer.Option("--seed", help="Override the session seed.")] = None,
    quantity: Annotated[
        int | None, typer.Option("--quantity", help="Parent size in base units.")
    ] = None,
    latency_ns: Annotated[
        int | None, typer.Option("--latency-ns", help="Network latency scenario.")
    ] = None,
    queue_policy: Annotated[
        str | None,
        typer.Option("--queue-policy", help="optimistic|neutral|conservative"),
    ] = None,
    configs_dir: ConfigDir = DEFAULT_CONFIG_DIR,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit JSON instead of tables.")
    ] = False,
) -> None:
    """Run one parent order and print its full TCA report."""
    configure_logging(level="WARNING")
    harness = harness_for(configs_dir)
    request = RunRequest(
        policy=policy,
        style=style,
        seed=seed,
        quantity_base=quantity,
        latency_ns=latency_ns,
        queue_policy=queue_policy,
        run_id=f"cli-{policy}-{style}",
    )
    context = harness.run(request)
    if context.tca is None or context.result is None:
        fail("the run produced no TCA report")
        return
    if json_output:
        typer.echo(json_dumps(context.tca.to_dict()))
        return

    report = context.tca
    typer.echo(render_table(["metric", "value"], sorted(report.headline.items())))
    typer.echo("")
    typer.echo("Benchmarks (ticks)")
    typer.echo(render_table(["benchmark", "value", "basis"], benchmark_rows(report)))
    typer.echo("")
    typer.echo("Cost attribution (bps, positive = worse)")
    typer.echo(render_table(["component", "bps"], attribution_rows(report)))
    typer.echo("")
    typer.echo("Markouts (bps, positive = adverse)")
    typer.echo(
        render_table(
            ["horizon_s", "measurable", "of_fills", "volume_weighted", "median"],
            [
                [
                    m.horizon_ns / 1e9,
                    m.n_measurable,
                    m.n_fills,
                    m.volume_weighted_bps,
                    m.median_bps,
                ]
                for m in report.markouts
            ],
        )
    )
    typer.echo("")
    typer.echo("Provenance")
    typer.echo(provenance_block(report.provenance))
    typer.echo("")
    typer.echo(caveat_block(report.caveats))


def demo(configs_dir: ConfigDir = DEFAULT_CONFIG_DIR) -> None:
    """Run the four algorithms on synthetic data and compare them.

    Works with zero downloads and zero API keys. The data is SYNTHETIC and every
    line of output says so.
    """
    configure_logging(level="WARNING")
    harness = harness_for(configs_dir)
    rows: list[list[Any]] = []
    for policy in ("twap", "vwap", "pov", "is_baseline"):
        for style in ("passive", "aggressive"):
            context = harness.run(
                RunRequest(policy=policy, style=style, run_id=f"demo-{policy}-{style}")
            )
            report = context.tca
            if report is None:
                continue
            metrics = report.metrics
            rows.append(
                [
                    policy,
                    style,
                    f"{metrics.fill_ratio:.2%}",
                    f"{metrics.maker_fill_ratio:.1%}",
                    metrics.cost_vs_arrival_bps,
                    metrics.cost_vs_vwap_bps,
                    metrics.implementation_shortfall_bps,
                    f"{metrics.participation_rate:.3%}",
                ]
            )
    typer.echo("SYNTHETIC DATA - deterministic generator, not a real venue.")
    typer.echo("")
    typer.echo(
        render_table(
            [
                "policy",
                "style",
                "fill",
                "maker",
                "vs_arrival_bps",
                "vs_vwap_bps",
                "is_bps",
                "participation",
            ],
            rows,
        )
    )
    typer.echo("")
    typer.echo(
        "Read `vs_arrival` and `vs_vwap` together. When they disagree, the arrival\n"
        "benchmark is crediting the strategy with market drift that happened while\n"
        "the order was working, not with execution skill."
    )


def report(
    policies: Annotated[
        str, typer.Option("--policies", help="Comma-separated list.")
    ] = "twap,vwap,pov,is_baseline",
    styles: Annotated[
        str, typer.Option("--styles", help="Comma-separated list.")
    ] = "passive,aggressive",
    seed: Annotated[int | None, typer.Option("--seed")] = None,
    output: Annotated[Path, typer.Option("--output")] = Path("artifacts/reports"),
    experiments: Annotated[
        bool,
        typer.Option(
            "--experiments/--no-experiments",
            help="Also run the experiment grids and render their report.",
        ),
    ] = False,
    ml: Annotated[
        bool,
        typer.Option(
            "--ml/--no-ml",
            help="Also fit the ML baselines and render their report.",
        ),
    ] = False,
    seeds: Annotated[
        int, typer.Option("--seeds", help="Sessions per cell for the experiment grid.")
    ] = 3,
    configs_dir: ConfigDir = DEFAULT_CONFIG_DIR,
) -> None:
    """Render self-contained HTML reports.

    One file per execution, plus an index. No JavaScript framework and no CDN:
    the output opens from disk and renders offline.

    `--experiments` and `--ml` are opt-in because both re-run real work: the
    grids execute every cell, and the ML report fits four models. The default
    stays fast so `make report` is usable in a loop.
    """
    configure_logging(level="WARNING")
    configs = harness_for(configs_dir).configs
    harness = ExecutionHarness(configs)
    builder = HtmlReportBuilder(output)
    rendered = []

    for policy in [p.strip() for p in policies.split(",") if p.strip()]:
        for style in [s.strip() for s in styles.split(",") if s.strip()]:
            run_id = f"{policy}-{style}"
            context = harness.run(RunRequest(policy=policy, style=style, seed=seed, run_id=run_id))
            if context.tca is None:
                typer.secho(f"  {run_id}: no TCA report, skipped", fg=typer.colors.YELLOW)
                continue
            # Pass the TCA report's provenance, not the harness's. Only the
            # former carries queue_mode / latency_basis / counterfactual_mode,
            # which the report prints as a badge above every number. Using the
            # harness provenance left that badge reading "UNKNOWN" while the
            # headline table below it said "APPROXIMATE".
            rendered.append(
                builder.execution_report(
                    context.tca, run_id=run_id, provenance=context.tca.provenance
                )
            )
            is_bps = context.tca.metrics.implementation_shortfall_bps
            typer.echo(f"  {run_id}: {is_bps:+.3f} bps IS")

    if experiments:
        rendered.extend(_render_experiments(builder, harness, configs, seeds=seeds))
    if ml:
        rendered.extend(_render_ml(builder, configs))

    if not rendered:
        fail("no reports were produced")
        return
    index = builder.index(rendered)
    typer.echo("")
    typer.echo(f"{len(rendered)} report(s) written to {builder.output_dir}")
    typer.echo(f"index: {index.path}")


def _render_experiments(
    builder: HtmlReportBuilder, harness: ExecutionHarness, configs: dict[str, Any], *, seeds: int
) -> list[Any]:
    """Run every pre-registered grid and render one report per grid."""
    from ...research import build_experiment_specs, run_experiment
    from ...research.registry import EnvironmentRecord

    base_seed = int(configs["market_data"]["source"]["options"].get("seed", 20260908))
    seed_list = [base_seed + i for i in range(max(seeds, 1))]
    environment = EnvironmentRecord.capture(Path(".")).to_dict()
    if len(seed_list) < 2:
        typer.secho(
            "  note: a single session gives a zero-width interval, so every "
            "comparison will appear to exclude zero. Run with --seeds 3 before "
            "reading anything into the counts below.",
            fg=typer.colors.YELLOW,
        )
    out = []
    for spec in build_experiment_specs(configs).values():
        result = run_experiment(harness, spec, seeds=seed_list)
        out.append(builder.experiment_report(result, environment=environment))
        significant = sum(1 for c in result.comparisons if c.significant)
        typer.echo(
            f"  {spec.name}: {len(result.cells)} cells, "
            f"{len(result.comparisons)} comparisons over {len(seed_list)} session(s), "
            f"{significant} interval(s) excluding zero"
        )
    return out


def _render_ml(builder: HtmlReportBuilder, configs: dict[str, Any]) -> list[Any]:
    """Fit the fill-probability baselines and render their report."""
    from ...data.registry import create_adapter
    from ...ml import (
        FillDatasetBuilder,
        MlConfig,
        SampleConfig,
        run_fill_probability_experiment,
    )

    ml_config = MlConfig.from_dict(configs)
    source = create_adapter(
        configs["market_data"]["source"]["adapter"],
        dict(configs["market_data"]["source"].get("options", {})),
    )
    dataset = FillDatasetBuilder(
        symbol=configs["market_data"]["instrument"]["symbol"],
        config=SampleConfig(
            sample_interval_ns=ml_config.sample_interval_ns,
            horizon_ns=ml_config.horizon_ns,
        ),
    )
    dataset.run(source.events())
    result = run_fill_probability_experiment(
        dataset.samples, config=ml_config, dataset_report=dataset.report.to_dict()
    )
    report = builder.ml_report(result)
    # Report the direction, not just whether zero is excluded. "One model's lift
    # is distinguishable from zero" reads as an endorsement; on the bundled data
    # the one such model is significantly *worse* than the base rate.
    better = sum(
        1 for r in result.test_reports if r.lift_is_distinguishable and r.lift_ci_lower > 0
    )
    worse = sum(1 for r in result.test_reports if r.lift_is_distinguishable and r.lift_ci_upper < 0)
    indifferent = len(result.test_reports) - better - worse
    typer.echo(
        f"  ml fill-probability, test split: {len(result.test_reports)} models - "
        f"{better} better than the base rate by a margin distinguishable from noise, "
        f"{worse} worse, {indifferent} indistinguishable"
    )
    return [report]


def register(app: typer.Typer) -> None:
    """Attach the execution-facing commands to the root app."""
    app.command()(version)
    app.command()(doctor)
    app.command()(run)
    app.command()(demo)
    app.command()(report)


def render_report_summary(report: Any) -> str:
    """Headline block, used by the API and by tests."""
    return render_mapping(report.headline)
