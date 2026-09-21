"""Commands that inspect and validate market data sources."""

from __future__ import annotations

from typing import Annotated

import typer

from ...data.registry import available_adapters, create_adapter
from ...data.validation import EventValidator, ValidationSettings
from ...domain.enums import ValidationMode
from ..console import render_mapping, render_table
from .shared import DEFAULT_CONFIG_DIR, ConfigDir, harness_for

__all__ = ["adapters", "inspect_source", "register"]

data_app = typer.Typer(help="Inspect and validate market data sources.", no_args_is_help=True)


@data_app.command("adapters")
def adapters() -> None:
    """List adapters and the capability tier each one declares."""
    rows = [[name, tier] for name, tier in sorted(available_adapters().items())]
    typer.echo(render_table(["adapter", "declared_data_type"], rows))
    typer.echo(
        "\nA declared tier is a claim about the source. Requesting a capability "
        "beyond it raises rather than degrading silently."
    )


@data_app.command("inspect")
def inspect_source(
    adapter: Annotated[str, typer.Option("--adapter")] = "synthetic",
    limit: Annotated[int, typer.Option("--limit", help="Events to read.")] = 2000,
    configs_dir: ConfigDir = DEFAULT_CONFIG_DIR,
) -> None:
    """Summarise a source: declared tier, event mix, and validation issues."""
    configs = harness_for(configs_dir).configs
    options = dict(configs["market_data"]["source"].get("options", {}))
    source = create_adapter(adapter, options)
    validator = EventValidator(ValidationSettings(mode=ValidationMode.WARN))

    counts: dict[str, int] = {}
    trades = 0
    for index, event in enumerate(validator.validate(source.events())):
        if index >= limit:
            break
        counts[event.event_type.value] = counts.get(event.event_type.value, 0) + 1
        if event.event_type.value == "TRADE":
            trades += 1

    typer.echo(f"adapter            : {source.name}")
    typer.echo(f"declared_data_type : {source.data_type.value}")
    typer.echo(f"events read        : {sum(counts.values())} (limit {limit})")
    typer.echo(f"trades             : {trades}")
    typer.echo("")
    typer.echo(render_table(["event_type", "count"], sorted(counts.items())))
    typer.echo("")
    typer.echo("Validation")
    typer.echo(render_mapping(validator.report.to_dict()))


def register(app: typer.Typer) -> None:
    app.add_typer(data_app, name="data")
