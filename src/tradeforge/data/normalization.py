"""Canonical serialization of normalized events.

One row shape, one place that writes it, one place that reads it. Everything
downstream (parquet artifacts, SQL, reports) uses this schema.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ..domain.enums import EventType, Side
from ..domain.events import MarketEvent

SCHEMA = pa.schema(
    [
        ("sequence_id", pa.int64()),
        ("exchange_timestamp_ns", pa.int64()),
        ("symbol", pa.string()),
        ("event_type", pa.string()),
        ("side", pa.string()),
        ("price_ticks", pa.int64()),
        ("quantity_base", pa.int64()),
        ("order_id", pa.int64()),
        ("trade_id", pa.int64()),
        ("flags", pa.int64()),
        ("source", pa.string()),
        ("receive_timestamp_ns", pa.int64()),
    ]
)


def event_to_row(event: MarketEvent) -> dict[str, Any]:
    return {
        "sequence_id": event.sequence_id,
        "exchange_timestamp_ns": event.exchange_timestamp_ns,
        "symbol": event.symbol,
        "event_type": event.event_type.value,
        "side": event.side.value if event.side else None,
        "price_ticks": event.price_ticks,
        "quantity_base": event.quantity_base,
        "order_id": event.order_id,
        "trade_id": event.trade_id,
        "flags": int(event.flags),
        "source": event.source,
        "receive_timestamp_ns": event.receive_timestamp_ns,
    }


def row_to_event(row: dict[str, Any]) -> MarketEvent:
    return MarketEvent(
        sequence_id=int(row["sequence_id"]),
        exchange_timestamp_ns=int(row["exchange_timestamp_ns"]),
        symbol=str(row["symbol"]),
        event_type=EventType(str(row["event_type"]).upper()),
        side=Side(str(row["side"]).upper()) if row.get("side") else None,
        price_ticks=_opt_int(row.get("price_ticks")),
        quantity_base=_opt_int(row.get("quantity_base")),
        order_id=_opt_int(row.get("order_id")),
        trade_id=_opt_int(row.get("trade_id")),
        flags=_opt_flags(row.get("flags")),
        source=str(row.get("source", "unknown")),
        receive_timestamp_ns=_opt_int(row.get("receive_timestamp_ns")),
    )


def events_to_table(events: Iterable[MarketEvent]) -> pa.Table:
    rows = [event_to_row(e) for e in events]
    return pa.Table.from_pylist(rows, schema=SCHEMA)


def write_parquet(events: Iterable[MarketEvent], path: str) -> int:
    table = events_to_table(events)
    pq.write_table(table, path)
    return table.num_rows


def read_parquet(path: str) -> Iterator[MarketEvent]:
    table = pq.read_table(path)
    for row in table.to_pylist():
        yield row_to_event(row)


def _opt_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _opt_flags(value: Any) -> int:
    from ..domain.enums import EventFlag

    return EventFlag(int(value)) if value is not None else EventFlag.NONE
