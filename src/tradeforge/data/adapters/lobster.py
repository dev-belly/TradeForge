"""LOBSTER adapter (L3 / market-by-order).

LOBSTER message files carry order identity, so this is one of the few widely
available sources where **exact FIFO queue** is legitimate.

Status: IMPLEMENTED, NOT VERIFIED against real LOBSTER files — LOBSTER data is
licensed and cannot be redistributed here. The parser follows the documented
format; users with their own subscription can point `message_path` at it.

Format (message file, no header):
    time(s since midnight, float), event_type, order_id, size, price, direction
    event_type: 1 submission, 2 partial cancellation, 3 deletion,
                4 visible execution, 5 hidden execution, 7 trading halt
    direction:  1 = buy order, -1 = sell order
    price:      dollars x 10000

Known limitation: LOBSTER's orderbook file contains levels but not order ids, so
the initial book can only be seeded as SNAPSHOT (L2) events, not as individual
orders. In MBO mode the book therefore starts empty and fills in from messages.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path

from ...domain.enums import DataType, EventFlag, EventType, Side
from ...domain.events import MarketEvent
from ...domain.exceptions import AdapterError
from .base import BaseAdapter

_LOBSTER_TYPE_MAP: dict[str, EventType] = {
    "1": EventType.ADD,
    "2": EventType.CANCEL,
    "3": EventType.CANCEL,
    "4": EventType.TRADE,
    "5": EventType.TRADE,
    "7": EventType.HALT,
}
_HIDDEN_EXECUTION = "5"


class LobsterAdapter(BaseAdapter):
    """Reads a LOBSTER message CSV (and optionally the orderbook CSV)."""

    def __init__(self, options: dict[str, object] | None = None) -> None:
        super().__init__(options)
        self._message_path = Path(self.opt_str("message_path", ""))
        if not self._message_path.exists():
            raise AdapterError(f"LOBSTER message file not found: {self._message_path}")
        orderbook = self.opt_str("orderbook_path", "")
        self._orderbook_path = Path(orderbook) if orderbook else None
        if self._orderbook_path is not None and not self._orderbook_path.exists():
            raise AdapterError(f"LOBSTER orderbook file not found: {self._orderbook_path}")

    @property
    def name(self) -> str:
        return "lobster"

    @property
    def data_type(self) -> DataType:
        return DataType.L3_MBO

    def events(self) -> Iterator[MarketEvent]:
        scale = self.opt_int("price_scale", 10000)
        tick_size = float(self.opt_str("tick_size", "0.01"))
        divisor = max(round(tick_size * scale), 1)
        symbol = self.opt_str("symbol", "UNKNOWN")
        base_ns = self.opt_int("base_date_ns", 0)
        limit = self.opt_int("max_rows", 0)

        if self._orderbook_path is not None:
            yield from self._initial_snapshot(symbol, base_ns, divisor)

        with self._message_path.open("r", newline="") as handle:
            reader = csv.reader(handle)
            for index, row in enumerate(reader):
                if limit and index >= limit:
                    return
                if len(row) < 6:
                    raise AdapterError(
                        f"{self._message_path}:{index} expected 6 columns, got {len(row)}"
                    )
                raw_time, raw_type, raw_oid, raw_size, raw_price, raw_dir = row[:6]
                event_type = _LOBSTER_TYPE_MAP.get(raw_type.strip())
                if event_type is None:
                    raise AdapterError(f"unknown LOBSTER event type {raw_type!r} at row {index}")
                seconds = float(raw_time)
                timestamp_ns = base_ns + round(seconds * 1_000_000_000)
                direction = int(raw_dir)
                side = Side.BUY if direction >= 0 else Side.SELL
                price_ticks = round(float(raw_price) / divisor)
                quantity = int(raw_size)
                flags = EventFlag.NONE
                if event_type is EventType.TRADE:
                    # LOBSTER direction on an execution is the aggressor side.
                    flags |= (
                        EventFlag.AGGRESSOR_BUY if side is Side.BUY else EventFlag.AGGRESSOR_SELL
                    )
                    if raw_type.strip() == _HIDDEN_EXECUTION:
                        flags |= EventFlag.HIDDEN
                yield MarketEvent(
                    sequence_id=index,
                    exchange_timestamp_ns=timestamp_ns,
                    symbol=symbol,
                    event_type=event_type,
                    side=side,
                    price_ticks=price_ticks,
                    quantity_base=quantity,
                    order_id=int(raw_oid),
                    trade_id=index if event_type is EventType.TRADE else None,
                    flags=flags,
                    source=self.name,
                )

    def _initial_snapshot(self, symbol: str, base_ns: int, divisor: int) -> Iterator[MarketEvent]:
        """Seed levels from the orderbook file as SNAPSHOT events (L2 semantics)."""
        assert self._orderbook_path is not None
        depth = self.opt_int("orderbook_depth", 10)
        with self._orderbook_path.open("r", newline="") as handle:
            reader = csv.reader(handle)
            row = next(reader, None)
            if row is None:
                return
            values = [float(v) for v in row]
        seq = 0
        for level in range(depth):
            base = level * 4
            if base + 3 >= len(values):
                break
            ask_price, ask_size, bid_price, bid_size = values[base : base + 4]
            yield MarketEvent(
                sequence_id=-1000 + seq,
                exchange_timestamp_ns=base_ns,
                symbol=symbol,
                event_type=EventType.SNAPSHOT,
                side=Side.BUY,
                price_ticks=round(bid_price / divisor),
                quantity_base=int(bid_size),
                source=self.name,
            )
            seq += 1
            yield MarketEvent(
                sequence_id=-1000 + seq,
                exchange_timestamp_ns=base_ns,
                symbol=symbol,
                event_type=EventType.SNAPSHOT,
                side=Side.SELL,
                price_ticks=round(ask_price / divisor),
                quantity_base=int(ask_size),
                source=self.name,
            )
            seq += 1
