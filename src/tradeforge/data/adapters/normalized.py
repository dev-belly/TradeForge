"""Reader for TradeForge's canonical normalized event files.

This is the format every other adapter produces, and the one `book replay`
consumes by default. Stored as Parquet (preferred) or CSV.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from ...domain.enums import DataType, EventFlag, EventType, Side
from ...domain.events import MarketEvent
from ...domain.exceptions import AdapterError
from .base import BaseAdapter


def _parse_side(value: str) -> Side | None:
    if not value or value in {"", "None", "null", "nan"}:
        return None
    return Side(value.upper())


class NormalizedAdapter(BaseAdapter):
    """Reads normalized events from Parquet or CSV."""

    def __init__(self, options: dict[str, object] | None = None) -> None:
        super().__init__(options)
        path = self.opt_str("path", "")
        if not path:
            raise AdapterError("normalized adapter requires options.path")
        self._path = Path(path)
        if not self._path.exists():
            raise AdapterError(f"normalized event file not found: {self._path}")
        suffix = self._path.suffix.lower()
        self._format = self.opt_str("format", "parquet" if suffix == ".parquet" else "csv")
        if self._format not in {"parquet", "csv"}:
            raise AdapterError(f"unsupported format {self._format!r}")

    @property
    def name(self) -> str:
        return "normalized"

    @property
    def data_type(self) -> DataType:
        return DataType(self.opt_str("data_type", "L2_MBP").upper())

    def events(self) -> Iterator[MarketEvent]:
        if self._format == "parquet":
            yield from self._read_parquet()
        else:
            yield from self._read_csv()

    def _read_parquet(self) -> Iterator[MarketEvent]:
        import pyarrow.parquet as pq

        table = pq.read_table(self._path)
        columns = {name: table.column(name).to_pylist() for name in table.column_names}
        for i in range(table.num_rows):
            yield self._row_to_event({name: values[i] for name, values in columns.items()})

    def _read_csv(self) -> Iterator[MarketEvent]:
        with self._path.open("r", newline="") as handle:
            for row in csv.DictReader(handle):
                yield self._row_to_event(row)

    def _row_to_event(self, row: dict[str, object]) -> MarketEvent:
        def as_int(key: str) -> int | None:
            value = row.get(key)
            if value is None or value == "" or value == "None":
                return None
            return int(str(value))

        return MarketEvent(
            sequence_id=int(str(row["sequence_id"])),
            exchange_timestamp_ns=int(str(row["exchange_timestamp_ns"])),
            symbol=str(row["symbol"]),
            event_type=EventType(str(row["event_type"]).upper()),
            side=_parse_side(str(row.get("side", ""))),
            price_ticks=as_int("price_ticks"),
            quantity_base=as_int("quantity_base"),
            order_id=as_int("order_id"),
            trade_id=as_int("trade_id"),
            flags=EventFlag(int(str(row.get("flags", 0) or 0))),
            source=str(row.get("source", "normalized")),
            receive_timestamp_ns=as_int("receive_timestamp_ns"),
        )
