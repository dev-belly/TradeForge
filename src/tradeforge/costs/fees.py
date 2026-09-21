"""Fee model. All parameters come from `configs/costs.yaml`."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

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


class FeeModel:
    """Exchange fees + commission. A maker rebate produces a negative fee."""

    def __init__(self, config: FeeConfig) -> None:
        self._config = config

    @property
    def config(self) -> FeeConfig:
        return self._config

    def fee(
        self, *, notional: Decimal, liquidity: LiquidityFlag, quantity_base: int = 0
    ) -> Decimal:
        if liquidity is LiquidityFlag.TAKER:
            rate_bps = self._config.taker_fee_bps
        elif liquidity is LiquidityFlag.MAKER:
            rate_bps = self._config.maker_fee_bps - self._config.maker_rebate_bps
        else:
            rate_bps = max(self._config.taker_fee_bps, self._config.maker_fee_bps)
        exchange_fee = notional * Decimal(str(rate_bps)) / Decimal("10000")
        commission = Decimal(str(self._config.commission_per_share)) * Decimal(quantity_base)
        total = exchange_fee + commission
        minimum = Decimal(str(self._config.min_commission_per_order))
        return max(total, minimum) if minimum > 0 else total

    def fee_bps(
        self, *, notional: Decimal, liquidity: LiquidityFlag, quantity_base: int = 0
    ) -> float:
        if notional == 0:
            return 0.0
        fee = self.fee(notional=notional, liquidity=liquidity, quantity_base=quantity_base)
        return float(fee / notional * Decimal("10000"))

    def describe(self) -> dict[str, float]:
        return {
            "taker_fee_bps": self._config.taker_fee_bps,
            "maker_fee_bps": self._config.maker_fee_bps,
            "maker_rebate_bps": self._config.maker_rebate_bps,
            "commission_per_share": self._config.commission_per_share,
        }
