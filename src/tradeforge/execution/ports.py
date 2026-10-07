"""Structural contracts consumed by the execution simulator."""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from ..domain.enums import LiquidityFlag


class FeeModelLike(Protocol):
    """A stateless fee quote with the child order's prior fill quantity."""

    def fee(
        self,
        *,
        notional: Decimal,
        liquidity: LiquidityFlag,
        quantity_base: int = 0,
        filled_before_base: int = 0,
    ) -> Decimal: ...
