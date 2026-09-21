"""Order book reconstruction (MBP / MBO)."""

from __future__ import annotations

from collections.abc import Callable

from ..domain.enums import BookMode, DataType
from ..domain.exceptions import DataCapabilityError
from ..domain.instrument import InstrumentSpec
from .config import BookSettings
from .invariants import InvariantViolation
from .levels import SideLevels
from .mbo_book import MboBook
from .mbp_book import MbpBook
from .metrics import (
    depth_base,
    depth_slope,
    microprice_ticks,
    mid_ticks,
    multi_level_imbalance,
    quoted_spread_ticks,
    relative_spread_bps,
    top_imbalance,
)

Book = MbpBook | MboBook


def create_book(
    *,
    symbol: str,
    settings: BookSettings,
    data_type: DataType,
    spec: InstrumentSpec | None = None,
    on_violation: Callable[[InvariantViolation, str, object], None] | None = None,
) -> Book:
    """Build the book that the declared data capability can support.

    MBO is only available with L3 data. Requesting it with L2 raises rather than
    silently falling back to an MBP book that cannot give exact queue.
    """
    if settings.mode is BookMode.MBO and data_type is not DataType.L3_MBO:
        raise DataCapabilityError(
            f"book mode MBO requested but source declares {data_type.value}; "
            "exact order-level reconstruction requires L3/MBO data"
        )
    if settings.mode is BookMode.MBO:
        return MboBook(symbol, settings, data_type, spec, on_violation)
    return MbpBook(symbol, settings, spec, on_violation)


__all__ = [
    "Book",
    "BookSettings",
    "InvariantViolation",
    "MboBook",
    "MbpBook",
    "SideLevels",
    "create_book",
    "depth_base",
    "depth_slope",
    "microprice_ticks",
    "mid_ticks",
    "multi_level_imbalance",
    "quoted_spread_ticks",
    "relative_spread_bps",
    "top_imbalance",
]
