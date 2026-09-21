"""Storage: Parquet artefacts plus a DuckDB query layer over them.

Write path: `ParquetWriter`. Read path: `DuckDbStore`. The packaged SQL queries
live in the repository's `sql/` directory and are run by name.
"""

from .duckdb_store import DuckDbStore, StoreStatus
from .schema import TABLE_DDL, TABLE_ORDER
from .writer import ParquetWriter, WriteResult

__all__ = [
    "TABLE_DDL",
    "TABLE_ORDER",
    "DuckDbStore",
    "ParquetWriter",
    "StoreStatus",
    "WriteResult",
]
