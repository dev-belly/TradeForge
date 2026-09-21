"""Experiment registry: make every number in the README traceable.

A result is reproducible only if you can recover the code, the configuration,
the data provenance and the seeds that produced it. Each record carries all
four. If the git commit cannot be determined the field says `unknown` - it is
never guessed, because a wrong commit hash is worse than an absent one.
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..infrastructure.logging import get_logger, log_event
from .runner import ExperimentResult


def git_commit(cwd: Path | str | None = None) -> str:
    """Current commit hash, or 'unknown'. Never invents a value."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if completed.returncode != 0:
        return "unknown"
    return completed.stdout.strip() or "unknown"


def is_dirty(cwd: Path | str | None = None) -> bool:
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return bool(completed.stdout.strip())


@dataclass(frozen=True, slots=True)
class EnvironmentRecord:
    """Where the numbers were produced. Benchmarks without this are meaningless."""

    python_version: str
    platform: str
    machine: str
    processor: str
    cpu_count: int
    recorded_at_utc: str
    git_commit: str
    git_dirty: bool

    @classmethod
    def capture(cls, cwd: Path | str | None = None) -> EnvironmentRecord:
        import os

        return cls(
            python_version=sys.version.split()[0],
            platform=platform.platform(),
            machine=platform.machine(),
            processor=platform.processor() or "unknown",
            cpu_count=os.cpu_count() or 0,
            recorded_at_utc=datetime.now(tz=UTC).isoformat(),
            git_commit=git_commit(cwd),
            git_dirty=is_dirty(cwd),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExperimentRecord:
    """One experiment's full result plus the conditions it was produced under."""

    name: str
    config_fingerprint: str
    seeds: tuple[int, ...]
    metric: str
    summary: dict[str, Any]
    rows: tuple[dict[str, Any], ...]
    environment: EnvironmentRecord
    caveats: tuple[str, ...] = ()
    recorded_at_utc: str = field(default_factory=lambda: datetime.now(tz=UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "config_fingerprint": self.config_fingerprint,
            "seeds": list(self.seeds),
            "metric": self.metric,
            "summary": self.summary,
            "n_rows": len(self.rows),
            "environment": self.environment.to_dict(),
            "caveats": list(self.caveats),
            "recorded_at_utc": self.recorded_at_utc,
        }


class ExperimentRegistry:
    """Writes experiment artefacts to disk as JSON (summary) and JSONL (rows).

    JSONL rather than a binary format for the rows so the raw output is
    inspectable with `grep` and diffable in review. The SQL layer ingests these
    files into DuckDB; it does not replace them.
    """

    def __init__(self, output_dir: Path | str, *, project_root: Path | str | None = None) -> None:
        self._dir = Path(output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._root = Path(project_root) if project_root else None
        self._logger = get_logger("registry")
        self._environment = EnvironmentRecord.capture(self._root)

    @property
    def output_dir(self) -> Path:
        return self._dir

    @property
    def environment(self) -> EnvironmentRecord:
        return self._environment

    def record(self, result: ExperimentResult) -> ExperimentRecord:
        record = ExperimentRecord(
            name=result.name,
            config_fingerprint=result.config_fingerprint,
            seeds=result.seeds,
            metric=result.metric,
            summary=result.to_dict(),
            rows=result.rows,
            environment=self._environment,
            caveats=result.caveats,
        )
        self.write(record)
        return record

    def write(self, record: ExperimentRecord) -> tuple[Path, Path]:
        summary_path = self._dir / f"{record.name}.summary.json"
        rows_path = self._dir / f"{record.name}.rows.jsonl"
        summary_path.write_text(
            json.dumps(record.to_dict(), indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        with rows_path.open("w", encoding="utf-8") as handle:
            for row in record.rows:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        log_event(
            self._logger,
            20,
            "experiment recorded",
            experiment=record.name,
            summary=str(summary_path),
            rows=len(record.rows),
        )
        return summary_path, rows_path

    def write_index(self, records: Iterable[ExperimentRecord]) -> Path:
        """One file listing every experiment run, for the README to reference."""
        payload = {
            "environment": self._environment.to_dict(),
            "experiments": [r.to_dict() for r in records],
        }
        path = self._dir / "index.json"
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
        return path


def read_rows(path: Path | str) -> list[dict[str, Any]]:
    """Read a `.rows.jsonl` artefact back."""
    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped:
            rows.append(json.loads(stripped))
    return rows


def summarise_cells(result: ExperimentResult) -> Sequence[dict[str, Any]]:
    """Compact per-cell table, for printing and for the README."""
    return [cell.to_dict() for cell in result.cells]
