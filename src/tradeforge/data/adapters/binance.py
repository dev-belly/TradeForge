"""Binance public L2 depth adapter (real market data, no API key).

Declares L2_MBP: aggregated levels only. No order identity, therefore **no exact
queue** — the approximate queue model is the only legal one for this source.

A REST depth endpoint returns a *snapshot*, not a diff, so each poll emits CLEAR
followed by one SNAPSHOT event per level. Consecutive snapshots are separated by
`poll_interval_s` of real network pacing (unrelated to the latency model, which
never sleeps).

Network required; marked `@network` in tests.
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Iterator
from typing import Any

from ...domain.enums import DataType, EventType, Side
from ...domain.events import MarketEvent
from ...domain.exceptions import AdapterError
from .base import BaseAdapter

_DEFAULT_URL = "https://api.binance.com/api/v3/depth"


class BinanceAdapter(BaseAdapter):
    """Fetches public L2 depth snapshots from Binance."""

    def __init__(self, options: dict[str, object] | None = None) -> None:
        super().__init__(options)
        self._symbol = self.opt_str("symbol", "BTCUSDT").upper()
        self._limit = self.opt_int("limit", 100)
        if self._limit not in (5, 10, 20, 50, 100, 500, 1000, 5000):
            raise AdapterError(f"unsupported Binance depth limit: {self._limit}")
        self._base_url = self.opt_str("base_url", _DEFAULT_URL)
        self._timeout_s = self.opt_float("timeout_s", 5.0)
        self._polls = self.opt_int("polls", 1)
        self._poll_interval_s = self.opt_float("poll_interval_s", 1.0)

    @property
    def name(self) -> str:
        return "binance"

    @property
    def data_type(self) -> DataType:
        return DataType.L2_MBP

    def events(self) -> Iterator[MarketEvent]:
        seq = 0
        for poll in range(self._polls):
            if poll > 0 and self._poll_interval_s > 0:
                # Network pacing only. The latency model never sleeps.
                time.sleep(self._poll_interval_s)
            payload = self._fetch()
            timestamp_ns = int(payload["server_time_ms"]) * 1_000_000
            yield MarketEvent(
                sequence_id=seq,
                exchange_timestamp_ns=timestamp_ns,
                symbol=self._symbol,
                event_type=EventType.CLEAR,
                source=self.name,
            )
            seq += 1
            for side, key in ((Side.BUY, "bids"), (Side.SELL, "asks")):
                for price_str, qty_str in payload[key]:
                    price_ticks = round(float(price_str) / self._tick_value())
                    quantity_base = round(float(qty_str) / self._lot_value())
                    if quantity_base <= 0 or price_ticks <= 0:
                        continue
                    yield MarketEvent(
                        sequence_id=seq,
                        exchange_timestamp_ns=timestamp_ns,
                        symbol=self._symbol,
                        event_type=EventType.SNAPSHOT,
                        side=side,
                        price_ticks=price_ticks,
                        quantity_base=quantity_base,
                        source=self.name,
                    )
                    seq += 1

    # ------------------------------------------------------------- internal

    def _tick_value(self) -> float:
        """Price unit of one tick in quote currency (from config, never assumed)."""
        return float(self.opt_str("tick_size", "0.01"))

    def _lot_value(self) -> float:
        """Base-asset unit of one quantity step (from config, never assumed)."""
        return float(self.opt_str("lot_size", "1"))

    def _fetch(self) -> dict[str, Any]:
        url = f"{self._base_url}?symbol={self._symbol}&limit={self._limit}"
        request = urllib.request.Request(url, headers={"User-Agent": "tradeforge/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # network/HTTP errors are all the same to us
            raise AdapterError(f"Binance depth request failed: {exc}") from exc
        if "bids" not in raw or "asks" not in raw:
            raise AdapterError(f"unexpected Binance payload: {list(raw)[:5]}")
        # The REST depth endpoint has no server time; use local wall clock and
        # say so. It is a *fetch* time, not an exchange event time.
        raw["server_time_ms"] = int(time.time() * 1000)
        return raw
