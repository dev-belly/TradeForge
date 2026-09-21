"""Normalized market event.

Every adapter produces this exact record. The domain never sees a
source-specific shape, and no adapter-specific logic exists anywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .enums import EventFlag, EventType, Side


@dataclass(frozen=True, slots=True)
class MarketEvent:
    """One normalized market event.

    Attributes:
        sequence_id: source-local monotonic identifier.
        exchange_timestamp_ns: venue event time in nanoseconds (int64).
        symbol: normalized symbol.
        event_type: normalized event type.
        side: BUY/SELL when meaningful, otherwise None.
        price_ticks: integer ticks (ADR-001).
        quantity_base: quantity in base-asset units.
        order_id: present only for MBO/L3 sources.
        trade_id: present only for TRADE events.
        flags: EventFlag bitfield.
        source: adapter provenance.
        receive_timestamp_ns: feed-handler time, or None when not provided.
            Never synthesized.
    """

    sequence_id: int
    exchange_timestamp_ns: int
    symbol: str
    event_type: EventType
    side: Side | None = None
    price_ticks: int | None = None
    quantity_base: int | None = None
    order_id: int | None = None
    trade_id: int | None = None
    flags: EventFlag = EventFlag.NONE
    source: str = "unknown"
    receive_timestamp_ns: int | None = None

    @property
    def sort_key(self) -> tuple[int, int]:
        """Ordering key. Same-timestamp events keep source sequence order."""
        return (self.exchange_timestamp_ns, self.sequence_id)

    @property
    def has_flag(self) -> Any:
        """Convenience accessor: ``event.has_flag(EventFlag.AGGRESSOR_BUY)``."""
        return self.flags.__contains__

    def aggressor_side(self) -> Side | None:
        """Aggressor side if the source declared it, otherwise None.

        We never infer the aggressor from price alone.
        """
        if self.flags & EventFlag.AGGRESSOR_BUY:
            return Side.BUY
        if self.flags & EventFlag.AGGRESSOR_SELL:
            return Side.SELL
        return None

    def with_flags(self, extra: EventFlag) -> MarketEvent:
        """Return a copy with additional flags set (used by REPAIR)."""
        from dataclasses import replace

        return replace(self, flags=self.flags | extra)

    def __str__(self) -> str:
        return (
            f"<{self.event_type.value} seq={self.sequence_id} "
            f"t={self.exchange_timestamp_ns} {self.side or '-'} "
            f"px={self.price_ticks} qty={self.quantity_base}>"
        )
