"""POV (percentage of volume): trade a fixed fraction of OBSERVED volume.

Only market volume already observed is used. The policy never sees future
volume, which is the whole point: `target = rate x observed_volume_since_last`.

Consequences that show up in results:
  * in quiet periods the order barely trades (high non-completion risk);
  * in busy periods it trades a lot (participation and impact rise).
"""

from __future__ import annotations

from ...domain.book import MarketState
from ...domain.orders import ChildOrder
from .base import ExecutionPolicyBase


class PovPolicy(ExecutionPolicyBase):
    def __init__(
        self,
        *,
        target_rate: float = 0.05,
        min_slice_base: int = 0,
        max_slice_base: int = 1_000_000,
        rebalance_interval_ns: int = 60_000_000_000,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        if not 0.0 < target_rate <= 1.0:
            raise ValueError("target_rate must be in (0, 1]")
        self._target_rate = target_rate
        self._min_slice_base = min_slice_base
        self._max_slice_base = max_slice_base
        self._rebalance_interval_ns = rebalance_interval_ns
        self._last_cumulative_volume = 0
        self._next_rebalance_ns = self._parent.start_ns
        self._slice_index = 0

    @property
    def name(self) -> str:
        return "pov"

    @property
    def target_rate(self) -> float:
        return self._target_rate

    def on_market_event(self, state: MarketState) -> list[ChildOrder]:
        if state.timestamp_ns < self._next_rebalance_ns:
            return []
        self._next_rebalance_ns = state.timestamp_ns + self._rebalance_interval_ns
        if self.parent_remaining_base <= 0:
            return []
        observed = max(state.market_volume_base - self._last_cumulative_volume, 0)
        self._last_cumulative_volume = state.market_volume_base
        if observed <= 0:
            return []
        desired = round(self._target_rate * observed)
        desired = max(desired, self._min_slice_base if observed > 0 else 0)
        desired = min(desired, self._max_slice_base)
        quantity = min(desired, self.parent_remaining_base)
        if quantity <= 0:
            return []
        order = self._emit(state=state, quantity_base=quantity, slice_index=self._slice_index)
        self._slice_index += 1
        return [order] if order else []

    def on_window_end(self, state: MarketState) -> list[ChildOrder]:
        """POV has no schedule, so it also needs the explicit window-end hook.

        Called once, after working orders were pulled, so `unfilled_base` is the
        true remainder. Emitting repeatedly here would be a bug: this hook is
        invoked exactly once by the simulator.
        """
        if self._end_of_window != "sweep_marketable":
            return []
        remaining = self.unfilled_base
        if remaining <= 0:
            return []
        order = self._aggressive(
            state=state, quantity_base=remaining, slice_index=self._slice_index
        )
        self._slice_index += 1
        return [order] if order else []
