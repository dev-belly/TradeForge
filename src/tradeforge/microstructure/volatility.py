"""Causal realized volatility.

Realized volatility over a trailing window, computed from mid-price log returns
sampled on event arrival. Only returns already observed enter the estimate:
the window is a causal deque, and the value returned at time t uses returns up
to and including t.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReturnSample:
    timestamp_ns: int
    log_return: float


class RealizedVolatility:
    """Rolling realized volatility in basis points (annualization optional)."""

    def __init__(self, window_ns: int = 600_000_000_000) -> None:
        self._window_ns = window_ns
        self._samples: deque[ReturnSample] = deque()
        self._prev_mid: float | None = None

    @property
    def window_ns(self) -> int:
        return self._window_ns

    def update(self, *, timestamp_ns: int, mid_ticks: float | None) -> None:
        if mid_ticks is None or mid_ticks <= 0:
            return
        if self._prev_mid is not None and self._prev_mid > 0:
            log_return = math.log(mid_ticks / self._prev_mid)
            self._samples.append(ReturnSample(timestamp_ns=timestamp_ns, log_return=log_return))
        self._prev_mid = mid_ticks
        self._evict(timestamp_ns)

    def volatility_bps(self, timestamp_ns: int | None = None) -> float:
        """sqrt(sum of squared log returns) over the window, in bps."""
        if timestamp_ns is not None:
            self._evict(timestamp_ns)
        if not self._samples:
            return 0.0
        total = sum(sample.log_return**2 for sample in self._samples)
        return math.sqrt(total) * 10_000.0

    def n_returns(self) -> int:
        return len(self._samples)

    def _evict(self, timestamp_ns: int) -> None:
        cutoff = timestamp_ns - self._window_ns
        while self._samples and self._samples[0].timestamp_ns < cutoff:
            self._samples.popleft()
