"""Simulation guardrails.

These are NOT a production risk system. They exist so an experiment cannot
silently produce nonsense (a 90% participation run, a 10x size typo) and so the
replay approximation stays inside the range where it is defensible.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.exceptions import GuardrailBreachError
from ..domain.instrument import InstrumentSpec
from ..domain.orders import ChildOrder


@dataclass(frozen=True)
class GuardConfig:
    max_child_order_base: int = 100_000
    max_notional: float = 10_000_000.0
    max_participation: float = 0.25
    max_open_orders: int = 50
    max_inventory_base: int = 1_000_000
    kill_switch_enabled: bool = True

    @classmethod
    def from_dict(cls, payload: dict[str, object] | None) -> GuardConfig:
        guards = payload.get("guards", {}) if isinstance(payload, dict) else {}
        if not isinstance(guards, dict):
            raise TypeError("replay config must contain a 'guards' mapping")
        return cls(
            max_child_order_base=int(str(guards.get("max_child_order_base", 100_000))),
            max_notional=float(str(guards.get("max_notional", 10_000_000.0))),
            max_participation=float(str(guards.get("max_participation", 0.25))),
            max_open_orders=int(str(guards.get("max_open_orders", 50))),
            max_inventory_base=int(str(guards.get("max_inventory_base", 1_000_000))),
            kill_switch_enabled=str(guards.get("kill_switch_enabled", True)).lower()
            in {"1", "true", "yes"},
        )


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str = ""


class Guardrails:
    """Checks applied to every new child order and to running participation."""

    def __init__(self, config: GuardConfig) -> None:
        self._config = config
        self._tripped = False
        self._trip_reason = ""
        self._breaches: list[str] = []

    @property
    def config(self) -> GuardConfig:
        return self._config

    @property
    def tripped(self) -> bool:
        return self._tripped

    @property
    def trip_reason(self) -> str:
        return self._trip_reason

    @property
    def breaches(self) -> tuple[str, ...]:
        return tuple(self._breaches)

    def check_new_order(
        self,
        order: ChildOrder,
        *,
        spec: InstrumentSpec,
        open_orders: int,
        inventory_base: int,
    ) -> GuardDecision:
        if self._tripped:
            return GuardDecision(False, f"kill switch tripped: {self._trip_reason}")
        if order.quantity_base <= 0:
            return GuardDecision(False, "non-positive quantity")
        if order.quantity_base > self._config.max_child_order_base:
            return GuardDecision(
                False,
                f"child size {order.quantity_base} exceeds max {self._config.max_child_order_base}",
            )
        if open_orders >= self._config.max_open_orders:
            return GuardDecision(False, f"open orders {open_orders} at limit")
        projected = abs(inventory_base + order.side.sign * order.quantity_base)
        if projected > self._config.max_inventory_base:
            return GuardDecision(
                False, f"inventory {projected} exceeds max {self._config.max_inventory_base}"
            )
        if order.price_ticks is not None:
            notional = float(spec.notional(order.price_ticks, order.quantity_base))
            if notional > self._config.max_notional:
                return GuardDecision(False, f"notional {notional:.2f} exceeds max")
        return GuardDecision(True)

    def check_participation(self, our_volume_base: int, market_volume_base: int) -> GuardDecision:
        if market_volume_base <= 0:
            return GuardDecision(True)
        rate = our_volume_base / market_volume_base
        if rate > self._config.max_participation:
            return GuardDecision(
                False,
                f"participation {rate:.4f} exceeds max {self._config.max_participation}",
            )
        return GuardDecision(True)

    def trip(self, reason: str) -> None:
        """Kill switch: stop trading for the remainder of the run."""
        self._breaches.append(reason)
        if self._config.kill_switch_enabled:
            self._tripped = True
            self._trip_reason = reason

    def raise_if_breached(self, decision: GuardDecision) -> None:
        if not decision.allowed:
            raise GuardrailBreachError(decision.reason)
