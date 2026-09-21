"""DuckDB query layer over the Parquet artefacts.

DuckDB reads the files in place: there is no load step and no second copy of the
data to fall out of sync. Views are created only for tables that actually have
files, and `missing_tables` records the rest, so a query that silently returns
zero rows because a table was never written is impossible to mistake for "no
results".
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain.exceptions import ConfigurationError
from .schema import TABLE_DDL, TABLE_ORDER

try:
    import duckdb

    _DUCKDB_AVAILABLE = True
except ImportError:  # pragma: no cover
    duckdb = None  # type: ignore[assignment]
    _DUCKDB_AVAILABLE = False


@dataclass(frozen=True, slots=True)
class StoreStatus:
    """Which tables have data, and where the query files came from."""

    root: str
    present_tables: tuple[str, ...]
    missing_tables: tuple[str, ...]
    sql_files: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "root": self.root,
            "present_tables": list(self.present_tables),
            "missing_tables": list(self.missing_tables),
            "sql_files": list(self.sql_files),
        }


class DuckDbStore:
    """Read-only analytics over the Parquet output directory."""

    def __init__(self, root: Path | str, *, sql_dir: Path | str | None = None) -> None:
        if not _DUCKDB_AVAILABLE:
            raise RuntimeError("duckdb is required for the SQL layer; install tradeforge[full]")
        self._root = Path(root)
        self._sql_dir = Path(sql_dir) if sql_dir else None
        self._connection: Any | None = None

    # ------------------------------------------------------------ connection

    @property
    def root(self) -> Path:
        return self._root

    def connect(self) -> Any:
        if self._connection is None:
            self._connection = duckdb.connect(database=":memory:")
            self._create_views(self._connection)
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> DuckDbStore:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _create_views(self, connection: Any) -> None:
        for table in TABLE_ORDER:
            directory = self._root / table
            if not directory.is_dir() or not any(directory.glob("*.parquet")):
                continue
            glob = str(directory / "*.parquet").replace("'", "''")
            connection.execute(
                f"CREATE OR REPLACE VIEW {table} AS "
                f"SELECT * FROM read_parquet('{glob}', union_by_name = true)"
            )

    def status(self) -> StoreStatus:
        present = tuple(
            table
            for table in TABLE_ORDER
            if (self._root / table).is_dir() and any((self._root / table).glob("*.parquet"))
        )
        return StoreStatus(
            root=str(self._root),
            present_tables=present,
            missing_tables=tuple(t for t in TABLE_ORDER if t not in present),
            sql_files=tuple(sorted(p.name for p in self._sql_files())),
        )

    def ensure_created(self) -> None:
        """Create empty physical tables for any schema table with no data.

        Used by `tradeforge db init` so the schema exists before the first run.
        """
        connection = self.connect()
        for table in TABLE_ORDER:
            connection.execute(TABLE_DDL[table])

    # --------------------------------------------------------------- queries

    def query(self, sql: str, parameters: Sequence[Any] | None = None) -> list[tuple[Any, ...]]:
        connection = self.connect()
        result = connection.execute(sql, list(parameters or []))
        return result.fetchall()

    def columns(self, sql: str) -> list[str]:
        connection = self.connect()
        description = connection.execute(sql).description
        return [d[0] for d in description]

    def query_df(self, sql: str) -> Any:
        """Return a pandas DataFrame. Requires pandas."""
        return self.connect().execute(sql).df()

    # ------------------------------------------------------------- sql files

    def _sql_files(self) -> list[Path]:
        if self._sql_dir is None or not self._sql_dir.is_dir():
            return []
        return sorted(self._sql_dir.glob("*.sql"))

    def available_queries(self) -> dict[str, Path]:
        return {path.stem: path for path in self._sql_files()}

    def load_query(self, name: str) -> str:
        queries = self.available_queries()
        key = name.removesuffix(".sql")
        if key not in queries:
            raise ConfigurationError(f"unknown query {name!r}; available: {sorted(queries)}")
        return queries[key].read_text(encoding="utf-8")

    def run_named(self, name: str) -> Any:
        return self.query_df(self.load_query(name))

    def run_all(self) -> dict[str, Any]:
        """Run every packaged query. Empty result frames are kept, not dropped."""
        return {name: self.run_named(name) for name in self.available_queries()}
