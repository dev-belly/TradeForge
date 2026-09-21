"""Order book settings. Every value comes from `configs/book.yaml`."""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import BookMode, ValidationMode


@dataclass(frozen=True)
class BookSettings:
    mode: BookMode = BookMode.MBP
    snapshot_depth: int = 10
    max_levels_per_side: int = 500
    modify_priority_policy: str = "LOSE_PRIORITY"
    unknown_aggressor_policy: str = "strict"  # strict | skip
    trade_price_tolerance_ticks: int = 0
    on_crossed_book: ValidationMode = ValidationMode.STRICT
    on_negative_depth: ValidationMode = ValidationMode.STRICT
    on_unknown_order_id: ValidationMode = ValidationMode.STRICT
    on_locked_market: ValidationMode = ValidationMode.WARN

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> BookSettings:
        book = payload.get("book", {}) if isinstance(payload, dict) else {}
        inv = payload.get("invariants", {}) if isinstance(payload, dict) else {}
        if not isinstance(book, dict) or not isinstance(inv, dict):
            raise TypeError("book config must contain 'book' and 'invariants' mappings")
        return cls(
            mode=BookMode(str(book.get("mode", "MBP")).upper()),
            snapshot_depth=int(str(book.get("snapshot_depth", 10))),
            max_levels_per_side=int(str(book.get("max_levels_per_side", 500))),
            modify_priority_policy=str(payload.get("modify_priority_policy", "LOSE_PRIORITY")),
            unknown_aggressor_policy=str(payload.get("unknown_aggressor_policy", "strict")),
            trade_price_tolerance_ticks=int(str(payload.get("trade_price_tolerance_ticks", 0))),
            on_crossed_book=ValidationMode(str(inv.get("on_crossed_book", "STRICT")).upper()),
            on_negative_depth=ValidationMode(str(inv.get("on_negative_depth", "STRICT")).upper()),
            on_unknown_order_id=ValidationMode(
                str(inv.get("on_unknown_order_id", "STRICT")).upper()
            ),
            on_locked_market=ValidationMode(str(inv.get("on_locked_market", "WARN")).upper()),
        )
