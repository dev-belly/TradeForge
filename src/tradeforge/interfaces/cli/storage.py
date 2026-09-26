"""Commands that query the Parquet artefacts through DuckDB."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ...storage import DuckDbStore
from ..console import render_mapping
from .shared import fail, first_question

__all__ = ["db_init", "db_list", "db_query", "register"]

db_app = typer.Typer(help="Query the Parquet artefacts with DuckDB.", no_args_is_help=True)

DEFAULT_ROOT = Path("artifacts/runs")
DEFAULT_SQL_DIR = Path("sql")


@db_app.command("init")
def db_init(root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT) -> None:
    """Create the Parquet directory layout and verify the schema compiles."""
    store = DuckDbStore(root, sql_dir=DEFAULT_SQL_DIR)
    store.ensure_created()
    typer.echo(render_mapping(store.status().to_dict()))


@db_app.command("list")
def db_list(
    sql_dir: Annotated[Path, typer.Option("--sql-dir")] = DEFAULT_SQL_DIR,
) -> None:
    """List the packaged analytical queries."""
    store = DuckDbStore(DEFAULT_ROOT, sql_dir=sql_dir)
    queries = store.available_queries()
    if not queries:
        fail(f"no .sql files found in {sql_dir}")
        return
    for name, path in sorted(queries.items()):
        typer.echo(f"{name}\n  {first_question(path)}\n  {path}")


@db_app.command("query")
def db_query(
    name: Annotated[str, typer.Argument(help="Query name, with or without .sql")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    sql_dir: Annotated[Path, typer.Option("--sql-dir")] = DEFAULT_SQL_DIR,
) -> None:
    """Run one packaged query and print the result.

    Checks the query's own table dependencies first. Letting DuckDB raise a
    `CatalogException` for a missing table reads as a broken query; in fact the
    table is expected to be absent until something writes it, and the useful
    thing to say is which command writes it.
    """
    store = DuckDbStore(root, sql_dir=sql_dir)
    status = store.status()
    if not status.present_tables:
        fail(f"no Parquet artefacts under {root}. Run `make run-all` first.")
        return

    missing = store.missing_tables_for(name)
    if missing:
        writers = {
            "events": "`make run-all` (drop --skip-events)",
            "experiment_runs": "`make run-all`, or `make research`",
            "datasets": "`make run-all`",
            "executions": "`make run-all`",
            "child_orders": "`make run-all`",
            "fills": "`make run-all`",
            "tca_metrics": "`make run-all`",
            "markouts": "`make run-all`",
        }
        fail(
            f"query {name!r} reads {', '.join(missing)}, which this store does not "
            "hold.\nRun: " + "; ".join(writers.get(t, "see docs") for t in missing)
        )
        return

    frame = store.run_named(name)
    typer.echo(f"root: {root}")
    typer.echo(f"reads: {', '.join(store.required_tables(name))}")
    if status.missing_tables:
        typer.echo(f"tables absent : {', '.join(status.missing_tables)}")
    typer.echo("")
    typer.echo(frame.to_string(index=False) if len(frame) else "(no rows)")


def register(app: typer.Typer) -> None:
    app.add_typer(db_app, name="db")
