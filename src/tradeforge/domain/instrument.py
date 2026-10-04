"""Instrument specification and price/quantity conversion.

Price inside the core is `int64` ticks (ADR-001). Conversion to float happens
only at reporting / ML boundaries, and always through this module so there is
exactly one place where the rule can be violated.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .exceptions import InstrumentError


def _decimal_times_int(value: Decimal, multiplier: int) -> Decimal:
    """Multiply a finite decimal coefficient without using arithmetic context."""
    if not value.is_finite():
        raise InstrumentError("decimal amount must be finite")
    parts = value.as_tuple()
    coefficient = 0
    for digit in parts.digits:
        coefficient = coefficient * 10 + digit
    digits = Decimal(coefficient * abs(multiplier)).as_tuple().digits
    sign = int(value.is_signed() != (multiplier < 0))
    return Decimal((sign, digits, int(parts.exponent)))


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
        if not self.tick_size.is_finite() or self.tick_size <= 0:
            raise InstrumentError(f"tick_size must be finite and positive, got {self.tick_size}")
        if self.lot_size <= 0:
            raise InstrumentError(f"lot_size must be positive, got {self.lot_size}")
        if self.price_band_lower_ticks >= self.price_band_upper_ticks:
            raise InstrumentError("price band lower must be below upper")

    # ---------------------------------------------------------------- price

    def price_to_ticks(self, price: float | str | Decimal) -> int:
        """Convert a human price to integer ticks, rounded half-up."""
        try:
            dec = price if isinstance(price, Decimal) else Decimal(str(price))
        except InvalidOperation as error:
            raise InstrumentError(f"invalid price: {price}") from error
        if not dec.is_finite():
            raise InstrumentError("price must be finite")
        numerator, denominator = dec.as_integer_ratio()
        tick_numerator, tick_denominator = self.tick_size.as_integer_ratio()
        numerator *= tick_denominator
        denominator *= tick_numerator
        whole, remainder = divmod(abs(numerator), denominator)
        rounded = whole + int(2 * remainder >= denominator)
        return -rounded if numerator < 0 else rounded

    def ticks_to_decimal(self, price_ticks: int) -> Decimal:
        """Exact price. Use for notional and for anything that is reported."""
        return _decimal_times_int(self.tick_size, price_ticks)

    def ticks_to_float(self, price_ticks: int) -> float:
        """Lossy conversion for reporting / plotting only.

        Never feed the result back into book or matching logic.
        """
        return float(self.ticks_to_decimal(price_ticks))

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
        return _decimal_times_int(self.tick_size, price_ticks * int(quantity_base))

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
