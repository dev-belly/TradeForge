"""Market data layer: adapters, validation, normalization."""

from .adapters.base import BaseAdapter
from .adapters.binance import BinanceAdapter
from .adapters.lobster import LobsterAdapter
from .adapters.normalized import NormalizedAdapter
from .adapters.synthetic import SyntheticAdapter
from .normalization import (
    SCHEMA,
    event_to_row,
    events_to_table,
    read_parquet,
    row_to_event,
    write_parquet,
)
from .registry import ADAPTERS, available_adapters, create_adapter
from .validation import (
    EventValidator,
    ValidationIssue,
    ValidationReport,
    ValidationSettings,
)

__all__ = [
    "ADAPTERS",
    "SCHEMA",
    "BaseAdapter",
    "BinanceAdapter",
    "EventValidator",
    "LobsterAdapter",
    "NormalizedAdapter",
    "SyntheticAdapter",
    "ValidationIssue",
    "ValidationReport",
    "ValidationSettings",
    "available_adapters",
    "create_adapter",
    "event_to_row",
    "events_to_table",
    "read_parquet",
    "row_to_event",
    "write_parquet",
]
