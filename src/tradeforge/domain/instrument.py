"""Instrument specification and price/quantity conversion.

Price inside the core is `int64` ticks (ADR-001). Conversion to float happens
only at reporting / ML boundaries, and always through this module so there is
exactly one place where the rule can be violated.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .exceptions import InstrumentError


@dataclass(frozen=True)
class InstrumentSpec:
    """Static instrument definition. Never hard-code these values in logic."""

    symbol: str
    tick_size: Decimal
    lot_size: int
    currency: str
    price_band_lower_ticks: int
    price_band_upper_ticks: int

    def __post_init__(self) -> None:
        if self.tick_size <= 0:
            raise InstrumentError(f"tick_size must be positive, got {self.tick_size}")
        if self.lot_size <= 0:
            raise InstrumentError(f"lot_size must be positive, got {self.lot_size}")
        if self.price_band_lower_ticks >= self.price_band_upper_ticks:
            raise InstrumentError("price band lower must be below upper")

    # ---------------------------------------------------------------- price

    def price_to_ticks(self, price: float | str | Decimal) -> int:
        """Convert a human price to integer ticks, rounded half-up."""
        dec = price if isinstance(price, Decimal) else Decimal(str(price))
        ticks = (dec / self.tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return int(ticks)

    def ticks_to_decimal(self, price_ticks: int) -> Decimal:
        """Exact price. Use for notional and for anything that is reported."""
        return Decimal(price_ticks) * self.tick_size

    def ticks_to_float(self, price_ticks: int) -> float:
        """Lossy conversion for reporting / plotting only.

        Never feed the result back into book or matching logic.
        """
        return float(Decimal(price_ticks) * self.tick_size)

    def validate_price_ticks(self, price_ticks: int) -> None:
        if not self.price_band_lower_ticks <= price_ticks <= self.price_band_upper_ticks:
            raise InstrumentError(
                f"price {price_ticks} ticks outside band "
                f"[{self.price_band_lower_ticks}, {self.price_band_upper_ticks}]"
            )

    # ------------------------------------------------------------- quantity

    def validate_quantity(self, quantity_base: int) -> None:
        if quantity_base <= 0:
            raise InstrumentError(f"quantity must be positive, got {quantity_base}")

    def round_to_lot(self, quantity_base: int) -> int:
        """Round down to the instrument lot size."""
        return (int(quantity_base) // self.lot_size) * self.lot_size

    # ------------------------------------------------------------- notional

    def notional(self, price_ticks: int, quantity_base: int) -> Decimal:
        return self.ticks_to_decimal(price_ticks) * Decimal(int(quantity_base))

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> InstrumentSpec:
        """Build from a config mapping. Raises ConfigurationError-compatible errors."""
        try:
            return cls(
                symbol=str(payload["symbol"]),
                tick_size=Decimal(str(payload["tick_size"])),
                lot_size=int(str(payload["lot_size"])),
                currency=str(payload["currency"]),
                price_band_lower_ticks=int(str(payload["price_band_lower_ticks"])),
                price_band_upper_ticks=int(str(payload["price_band_upper_ticks"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise InstrumentError(f"invalid instrument config: {exc}") from exc
