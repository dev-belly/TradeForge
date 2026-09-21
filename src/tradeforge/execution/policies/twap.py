"""TWAP: uniform slices across the execution window.

Handles: lot rounding (residual lands on the last slice), missed slices and
late fills (cumulative-target rule), and window-end handling for whatever is
left.
"""

from __future__ import annotations

from .base import Schedule, ScheduledPolicy


class TwapPolicy(ScheduledPolicy):
    @property
    def name(self) -> str:
        return "twap"

    def build_schedule(self) -> Schedule:
        weights = [1.0 / self._n_slices] * self._n_slices
        return Schedule(
            slice_times_ns=self._slice_times(),
            cumulative_targets=self._cumulative_from_weights(weights),
        )
