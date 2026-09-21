"""Helpers shared by the CLI command modules.

Kept in one place so the command modules stay readable and so the option type is
declared once. Nothing here prints a number; it only decides how a number is
presented.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from ...application.harness import ExecutionHarness
from ...domain.exceptions import TradeForgeError
from ...infrastructure.config import DEFAULT_CONFIG_DIR, load_configs, validate_required

#: The one place the `--configs` option is declared. Re-annotating it at a call
#: site produces a Typer MultipleTyperAnnotationsError, so it is a named type.
ConfigDir = Annotated[Path, typer.Option("--configs", help="Configuration directory.")]

__all__ = [
    "DEFAULT_CONFIG_DIR",
    "ConfigDir",
    "attribution_rows",
    "benchmark_rows",
    "fail",
    "first_question",
    "harness_for",
    "json_dumps",
    "load_all_configs",
]


def load_all_configs(configs_dir: Path) -> dict[str, Any]:
    """Load and validate every configuration file, or raise."""
    configs = load_configs(configs_dir)
    validate_required(configs)
    return configs


def harness_for(configs_dir: Path) -> ExecutionHarness:
    return ExecutionHarness(load_all_configs(configs_dir))


def fail(message: str, code: int = 1) -> None:
    """Print an error and exit non-zero. Used for user-facing failures."""
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code)


def json_dumps(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def benchmark_rows(report: Any) -> list[list[Any]]:
    """Benchmark table rows, each naming its own basis."""
    benchmarks = report.benchmarks
    return [
        ["arrival_mid", benchmarks.arrival_mid_ticks, "first mid at/after window start"],
        [
            "interval_vwap",
            benchmarks.interval_vwap_ticks,
            f"{benchmarks.n_trade_prints} prints",
        ],
        ["interval_twap", benchmarks.interval_twap_ticks, "time weighted mid"],
        ["interval_mid", benchmarks.interval_mid_ticks, "unweighted mean of mids"],
        ["terminal_mid", benchmarks.terminal_mid_ticks, "last mid inside the window"],
    ]


def attribution_rows(report: Any) -> list[list[Any]]:
    """Cost attribution rows, with the residual labelled as unexplained."""
    attribution = report.attribution
    return [
        ["is_filled", attribution.is_filled_bps],
        ["spread_cost", attribution.spread_cost_bps],
        ["fees", attribution.fees_bps],
        ["timing", attribution.timing_bps],
        ["residual (unexplained)", attribution.residual_impact_bps],
        ["opportunity_cost", attribution.opportunity_cost_bps],
        ["is_total", attribution.is_total_bps],
    ]


def first_question(path: Path) -> str:
    """The `-- Question:` header of a packaged SQL file."""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("-- Question:"):
            return stripped.removeprefix("-- Question:").strip()
        if stripped.startswith("--"):
            continue
        break
    return "(no description)"


def guarded(callable_: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a harness call, turning a domain error into a clean CLI failure."""
    try:
        return callable_(*args, **kwargs)
    except TradeForgeError as exc:
        fail(str(exc))
        return None
