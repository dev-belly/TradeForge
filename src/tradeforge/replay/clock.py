"""Simulation clock and scheduling.

The clock is the only notion of "now" in the system. Actions (order arrivals,
timeouts, slice boundaries) are scheduled on it, which is how latency is
modelled: an order decided at `t` arrives at `t + Δ` **in simulation time**,
while market events keep flowing in between. Nothing ever sleeps.
"""

from __future__ import annotations

import heapq
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

ScheduledAction = Callable[[int], None]


@dataclass(order=True, slots=True)
class _ScheduledItem:
    due_ns: int
    tie_break: int
    action: ScheduledAction = field(compare=False)
    label: str = field(default="", compare=False)


class SimulationClock:
    """Monotonic clock with a priority queue of scheduled actions."""

    def __init__(self, start_ns: int = 0) -> None:
        self._now_ns = start_ns
        self._queue: list[_ScheduledItem] = []
        self._counter = 0

    @property
    def now_ns(self) -> int:
        return self._now_ns

    def advance_to(self, timestamp_ns: int) -> None:
        if timestamp_ns < self._now_ns:
            raise ValueError(f"clock cannot go backwards: {timestamp_ns} < {self._now_ns}")
        self._now_ns = timestamp_ns

    def schedule(self, due_ns: int, action: ScheduledAction, label: str = "") -> int:
        if due_ns < self._now_ns:
            raise ValueError(f"cannot schedule in the past: {due_ns} < {self._now_ns}")
        self._counter += 1
        heapq.heappush(
            self._queue,
            _ScheduledItem(due_ns=due_ns, tie_break=self._counter, action=action, label=label),
        )
        return self._counter

    def pop_due(self, until_ns: int | None = None) -> Iterator[_ScheduledItem]:
        """Yield and remove every action due at or before `until_ns` (default now)."""
        limit = self._now_ns if until_ns is None else until_ns
        while self._queue and self._queue[0].due_ns <= limit:
            yield heapq.heappop(self._queue)

    @property
    def pending(self) -> int:
        return len(self._queue)

    def next_due_ns(self) -> int | None:
        return self._queue[0].due_ns if self._queue else None
