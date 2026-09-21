"""Book invariants.

Checked after every mutating event. The mode (STRICT / WARN / REPAIR) comes
from configuration, never from a hard-coded branch in the book.
"""

from __future__ import annotations

from enum import Enum

from ..domain.enums import ValidationMode


class InvariantViolation(Enum):
    CROSSED_BOOK = "crossed_book"
    LOCKED_MARKET = "locked_market"
    NEGATIVE_DEPTH = "negative_depth"
    UNKNOWN_ORDER_ID = "unknown_order_id"
    TRADE_EXCEEDS_DEPTH = "trade_exceeds_depth"
    PRICE_OUT_OF_BAND = "price_out_of_band"


def check_book_shape(
    best_bid: int | None,
    best_ask: int | None,
    *,
    on_crossed: ValidationMode,
    on_locked: ValidationMode,
) -> list[tuple[InvariantViolation, str]]:
    """Return the violations present after an update.

    A locked market (bid == ask) is reported but is a real market state, not an
    error: some venues allow it, others would have matched. It is never
    auto-corrected.
    """
    violations: list[tuple[InvariantViolation, str]] = []
    if best_bid is None or best_ask is None:
        return violations
    if best_bid > best_ask:
        violations.append(
            (
                InvariantViolation.CROSSED_BOOK,
                f"best bid {best_bid} > best ask {best_ask}",
            )
        )
    elif best_bid == best_ask:
        violations.append(
            (
                InvariantViolation.LOCKED_MARKET,
                f"best bid == best ask == {best_bid}",
            )
        )
    return violations


def should_raise(
    violation: InvariantViolation, modes: dict[InvariantViolation, ValidationMode]
) -> bool:
    """STRICT raises; WARN/REPAIR are reported to the caller's hook."""
    mode = modes.get(violation, ValidationMode.STRICT)
    return mode is ValidationMode.STRICT
