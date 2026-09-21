"""Adapter registry.

The only place that maps a name to an adapter class. Adding a source means
adding a class under `adapters/` and one line here - never a branch in the core.
"""

from __future__ import annotations

from collections.abc import Callable

from ..domain.exceptions import AdapterError
from ..domain.protocols import MarketDataAdapter
from .adapters.base import BaseAdapter
from .adapters.binance import BinanceAdapter
from .adapters.lobster import LobsterAdapter
from .adapters.normalized import NormalizedAdapter
from .adapters.synthetic import SyntheticAdapter

ADAPTERS: dict[str, Callable[[dict[str, object] | None], BaseAdapter]] = {
    "synthetic": SyntheticAdapter,
    "synthetic-mbp": SyntheticAdapter,
    "normalized": NormalizedAdapter,
    "lobster": LobsterAdapter,
    "binance": BinanceAdapter,
}


def create_adapter(name: str, options: dict[str, object] | None = None) -> MarketDataAdapter:
    key = name.strip().lower()
    if key.startswith("synthetic-mbo"):
        merged = dict(options or {})
        merged["mode"] = "mbo"
        return SyntheticAdapter(merged)
    if key not in ADAPTERS:
        raise AdapterError(f"unknown adapter {name!r}; available: {sorted(ADAPTERS)}")
    adapter = ADAPTERS[key](options)
    if not isinstance(adapter, MarketDataAdapter):
        raise AdapterError(f"adapter {name!r} does not satisfy MarketDataAdapter")
    return adapter


def available_adapters() -> dict[str, str]:
    """Name -> declared capability tier, for `tradeforge data inspect`."""
    info: dict[str, str] = {}
    for name, factory in ADAPTERS.items():
        try:
            info[name] = factory({"mode": "mbp"}).data_type.value
        except Exception:  # adapters that need a real path cannot be probed
            info[name] = "unknown"
    return info
