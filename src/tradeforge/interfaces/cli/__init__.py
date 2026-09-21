"""Command line interface.

Split by responsibility rather than kept in one file: the execution commands, the
data inspection commands, the research and ML commands, and the storage commands
each live in their own module. `cli.py` was 601 lines when it was one file, which
is one line past the project's own limit and, more importantly, four jobs in one
place.

Every command that prints a cost number also prints the provenance that makes it
interpretable.
"""

from __future__ import annotations

import typer

from . import execution, market_data, research, storage

app = typer.Typer(
    name="tradeforge",
    help="Market microstructure, LOB, execution and TCA research platform.",
    no_args_is_help=True,
    add_completion=False,
)

execution.register(app)
market_data.register(app)
research.register(app)
storage.register(app)


def main() -> None:
    app()


__all__ = ["app", "main"]
