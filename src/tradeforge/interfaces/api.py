"""HTTP API (FastAPI).

The API is a thin shell over the application layer. It contains no market logic
and no formatting logic: it validates a request, calls the harness, and returns
the dataclasses' own `to_dict()`. Anything cleverer would be a second
implementation of the research semantics, and two implementations drift.

Two deliberate constraints:

  * every response that carries a cost number also carries `provenance` and
    `caveats`, at the top level, not nested away;
  * an empty result set returns an empty list with a `note` explaining why,
    rather than a 404 that a caller might read as "the strategy failed".

Start it with `make api`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ..application.harness import ExecutionHarness, RunRequest
from ..data.registry import available_adapters
from ..domain.exceptions import TradeForgeError
from ..infrastructure.config import DEFAULT_CONFIG_DIR, load_configs, validate_required
from ..infrastructure.cpp_bridge import core_status
from ..research import build_experiment_specs
from ..storage import DuckDbStore

DEFAULT_ARTIFACT_ROOT = Path("artifacts/runs")
DEFAULT_SQL_DIR = Path("sql")

app = FastAPI(
    title="TradeForge",
    description=(
        "Market microstructure, limit order book, execution and TCA research. "
        "All bundled data is SYNTHETIC; every response carries its provenance."
    ),
    version="0.1.0",
)


class RunRequestBody(BaseModel):
    """One execution request. Defaults mirror `configs/execution.yaml`."""

    policy: str = Field(default="twap", description="twap | vwap | pov | is_baseline")
    style: str = Field(default="passive", description="passive | aggressive | adaptive")
    seed: int | None = Field(
        default=None, description="Session seed; same seed reproduces the run."
    )
    quantity_base: int | None = Field(default=None, gt=0)
    latency_ns: int | None = Field(default=None, ge=0, description="Latency scenario, nanoseconds.")
    queue_policy: str | None = Field(
        default=None, description="optimistic | neutral | conservative"
    )
    pov_target_rate: float | None = Field(default=None, gt=0.0, le=1.0)


@app.get("/health")
def health() -> dict[str, Any]:
    """Liveness plus the two things that change what a result means."""
    core = core_status()
    return {
        "status": "ok",
        "version": app.version,
        "engine_backend": core["backend"],
        "engine_detail": core["detail"],
    }


@app.get("/datasets/adapters")
def list_adapters() -> dict[str, Any]:
    """Adapters and the capability tier each one declares."""
    adapters = available_adapters()
    return {
        "adapters": adapters,
        "note": (
            "A declared tier is a claim about the source. Requesting a capability "
            "beyond it raises rather than degrading silently."
        ),
    }


@app.get("/configs/experiments")
def list_experiments() -> dict[str, Any]:
    specs = build_experiment_specs(_configs())
    return {
        "experiments": [
            {
                "name": spec.name,
                "description": spec.description,
                "metric": spec.metric,
                "baseline": spec.baseline,
                "cells": list(spec.labels),
            }
            for spec in specs.values()
        ]
    }


@app.post("/executions")
def create_execution(body: RunRequestBody) -> dict[str, Any]:
    """Run one parent order and return its execution plus TCA report."""
    harness = _harness()
    request = RunRequest(
        policy=body.policy,
        style=body.style,
        seed=body.seed,
        quantity_base=body.quantity_base,
        latency_ns=body.latency_ns,
        queue_policy=body.queue_policy,
        pov_target_rate=body.pov_target_rate,
        run_id=f"api-{body.policy}-{body.style}",
    )
    try:
        context = harness.run(request)
    except TradeForgeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if context.result is None or context.tca is None:
        raise HTTPException(status_code=500, detail="the run produced no TCA report")

    return {
        "execution": context.result.to_dict(),
        "tca": context.tca.to_dict(),
        "validation": context.validation.to_dict() if context.validation else None,
        "book_violations": list(context.outcome.book_violations),
        "provenance": context.provenance,
        "caveats": list(context.tca.caveats),
        "config_fingerprint": harness.fingerprint,
    }


@app.get("/queries")
def list_queries() -> dict[str, Any]:
    """The packaged analytical queries, with the question each one answers."""
    store = DuckDbStore(DEFAULT_ARTIFACT_ROOT, sql_dir=DEFAULT_SQL_DIR)
    queries = store.available_queries()
    return {
        "queries": [
            {"name": name, "question": _first_question(path), "path": str(path)}
            for name, path in sorted(queries.items())
        ]
    }


@app.get("/queries/{name}")
def run_query(name: str) -> dict[str, Any]:
    """Run one packaged query over the Parquet artefacts.

    The checks run in order of what the caller can do about them, and the order
    matters. An earlier version tested for artefacts *before* validating the
    name, so `GET /queries/99_nope` answered 200 with an empty result and a
    "no artefacts" note on an empty store and 404 on a populated one. The same
    request described a nonexistent query as a query that found nothing, and the
    answer depended on unrelated state.
    """
    store = DuckDbStore(DEFAULT_ARTIFACT_ROOT, sql_dir=DEFAULT_SQL_DIR)

    # 1. Does the query exist? A property of the repository, not of the store.
    if name.removesuffix(".sql") not in store.available_queries():
        raise HTTPException(
            status_code=404,
            detail=f"unknown query {name!r}; available: {sorted(store.available_queries())}",
        )

    status = store.status()

    # 2. Are the tables it reads present? "events is missing" is actionable;
    #    DuckDB's CatalogException reads like a broken query.
    missing = store.missing_tables_for(name)
    if missing:
        return {
            "query": name,
            "rows": [],
            "note": (
                f"this query reads {', '.join(missing)}, which the store does not "
                f"hold. Run `make run-all` under {DEFAULT_ARTIFACT_ROOT}."
            ),
            "store": status.to_dict(),
        }

    # 3. Is there anything at all?
    if not status.present_tables:
        return {
            "query": name,
            "rows": [],
            "note": (
                f"no Parquet artefacts under {DEFAULT_ARTIFACT_ROOT}; run "
                "`make research` or `make run-all` first"
            ),
            "store": status.to_dict(),
        }

    try:
        frame = store.run_named(name)
    except TradeForgeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "query": name,
        "rows": frame.to_dict(orient="records"),
        "n_rows": len(frame),
        "store": status.to_dict(),
    }


@app.get("/reports/{name}", response_class=HTMLResponse)
def get_report(
    name: str, directory: Path = Query(default=Path("artifacts/reports"))
) -> HTMLResponse:
    """Serve a generated HTML report by filename."""
    candidate = (directory / name).resolve()
    root = directory.resolve()
    if root not in candidate.parents and candidate != root:
        raise HTTPException(status_code=400, detail="path escapes the report directory")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"no report named {name!r}")
    return HTMLResponse(candidate.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ helpers


def _configs() -> dict[str, Any]:
    configs = load_configs(DEFAULT_CONFIG_DIR)
    validate_required(configs)
    return configs


def _harness() -> ExecutionHarness:
    return ExecutionHarness(_configs())


def _first_question(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("-- Question:"):
            return stripped.removeprefix("-- Question:").strip()
    return "(no description)"


def main() -> None:  # pragma: no cover - manual entry point
    import uvicorn

    uvicorn.run("tradeforge.interfaces.api:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":  # pragma: no cover
    main()
