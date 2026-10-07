"""Fee model. All parameters come from `configs/costs.yaml`."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from ..domain.enums import LiquidityFlag


@dataclass(frozen=True)
class FeeConfig:
    taker_fee_bps: float = 0.0
    maker_fee_bps: float = 0.0
    maker_rebate_bps: float = 0.0
    commission_per_share: float = 0.0
    min_commission_per_order: float = 0.0

    @classmethod
    def from_dict(cls, payload: dict[str, object] | None) -> FeeConfig:
        fees = payload.get("fees", {}) if isinstance(payload, dict) else {}
        if not isinstance(fees, dict):
            raise TypeError("costs config must contain a 'fees' mapping")
        return cls(
            taker_fee_bps=float(str(fees.get("taker_fee_bps", 0.0))),
            maker_fee_bps=float(str(fees.get("maker_fee_bps", 0.0))),
            maker_rebate_bps=float(str(fees.get("maker_rebate_bps", 0.0))),
            commission_per_share=float(str(fees.get("commission_per_share", 0.0))),
            min_commission_per_order=float(str(fees.get("min_commission_per_order", 0.0))),
        )


def _exact_decimal(value: Fraction) -> Decimal:
    """Construct a terminating decimal without caller arithmetic settings."""
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise ValueError("Fee amount is not a terminating decimal")
    scale = max(twos, fives)
    coefficient = abs(value.numerator) * 2 ** (scale - twos) * 5 ** (scale - fives)
    digits = Decimal(coefficient).as_tuple().digits
    return Decimal((int(value < 0), digits, -scale))


class FeeModel:
    """Venue fees plus incremental commission for one child order.

    The caller supplies the quantity already filled on that order; quotes do
    not mutate the model. The commission minimum never floors venue rebates.
    """

    def __init__(self, config: FeeConfig) -> None:
        self._config = config

    @property
    def config(self) -> FeeConfig:
        return self._config

    def fee(
        self,
        *,
        notional: Decimal,
        liquidity: LiquidityFlag,
        quantity_base: int = 0,
        filled_before_base: int = 0,
    ) -> Decimal:
        if type(filled_before_base) is not int or filled_before_base < 0:
            raise ValueError("filled_before_base must be a non-negative integer")
        if liquidity is LiquidityFlag.TAKER:
            rate_bps = Fraction(str(self._config.taker_fee_bps))
        elif liquidity is LiquidityFlag.MAKER:
            rate_bps = Fraction(str(self._config.maker_fee_bps)) - Fraction(
                str(self._config.maker_rebate_bps)
            )
        else:
            rate_bps = max(
                Fraction(str(self._config.taker_fee_bps)),
                Fraction(str(self._config.maker_fee_bps)),
            )
        exchange_fee = Fraction(notional) * rate_bps / 10000
        per_share = Fraction(str(self._config.commission_per_share))
        commission = per_share * quantity_base
        minimum = Fraction(str(self._config.min_commission_per_order))
        if minimum > 0 and quantity_base > 0:
            due = max(per_share * (filled_before_base + quantity_base), minimum)
            paid = (
                max(per_share * filled_before_base, minimum)
                if filled_before_base > 0
                else Fraction(0)
            )
            commission = due - paid
        return _exact_decimal(exchange_fee + commission)

    def fee_bps(
        self,
        *,
        notional: Decimal,
        liquidity: LiquidityFlag,
        quantity_base: int = 0,
        filled_before_base: int = 0,
    ) -> float:
        fee = self.fee(
            notional=notional,
            liquidity=liquidity,
            quantity_base=quantity_base,
            filled_before_base=filled_before_base,
        )
        if notional == 0:
            return 0.0
        return float(Fraction(fee) / Fraction(notional) * 10000)

    def describe(self) -> dict[str, float]:
        return {
            "taker_fee_bps": self._config.taker_fee_bps,
            "maker_fee_bps": self._config.maker_fee_bps,
            "maker_rebate_bps": self._config.maker_rebate_bps,
            "commission_per_share": self._config.commission_per_share,
            "min_commission_per_order": self._config.min_commission_per_order,
        }
