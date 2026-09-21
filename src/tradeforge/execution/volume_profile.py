"""Historical volume profiles for VWAP scheduling.

Leakage rule: a profile must come from PRIOR sessions or from a causal
real-time estimator. Using the current session's realized (or full-day) volume
curve to schedule that same session is future leakage and raises
`LeakageError`.

For the synthetic demo, "prior sessions" are other synthetic sessions produced
by the same generator with different seeds. That is stated in the report - it is
not real market data and it is not the current session.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..domain.enums import EventType
from ..domain.events import MarketEvent
from ..domain.exceptions import LeakageError


@dataclass(frozen=True)
class VolumeProfile:
    """Normalized intraday volume weights (sum = 1)."""

    weights: tuple[float, ...]
    source: str
    n_sessions: int = 1

    def __post_init__(self) -> None:
        if not self.weights:
            raise ValueError("volume profile needs at least one bucket")
        if any(w < 0 for w in self.weights):
            raise ValueError("volume profile weights must be non-negative")

    @property
    def n_buckets(self) -> int:
        return len(self.weights)

    def for_slices(self, n_slices: int) -> tuple[float, ...]:
        """Resample the profile onto `n_slices` execution slices (normalized)."""
        if n_slices <= 0:
            raise ValueError("n_slices must be positive")
        if self.n_buckets == n_slices:
            return tuple(self.weights)
        scaled: list[float] = []
        for i in range(n_slices):
            start = i * self.n_buckets / n_slices
            end = (i + 1) * self.n_buckets / n_slices
            total = 0.0
            for bucket in range(int(start), max(int(end), int(start) + 1)):
                if bucket >= self.n_buckets:
                    break
                overlap = min(end, bucket + 1) - max(start, bucket)
                if overlap > 0:
                    total += self.weights[bucket] * overlap
            scaled.append(total)
        total_weight = sum(scaled)
        if total_weight <= 0:
            return tuple(1.0 / n_slices for _ in range(n_slices))
        return tuple(w / total_weight for w in scaled)


def compute_volume_profile(
    events: Iterable[MarketEvent],
    *,
    n_buckets: int,
    start_ns: int,
    end_ns: int,
    source: str = "unknown",
    n_sessions: int = 1,
) -> VolumeProfile:
    """Bucket traded volume between start and end. Purely historical input."""
    if end_ns <= start_ns:
        raise ValueError("end_ns must be after start_ns")
    buckets = [0.0] * n_buckets
    span = end_ns - start_ns
    for event in events:
        if event.event_type is not EventType.TRADE:
            continue
        if not start_ns <= event.exchange_timestamp_ns <= end_ns:
            continue
        position = (event.exchange_timestamp_ns - start_ns) / span
        index = min(int(position * n_buckets), n_buckets - 1)
        buckets[index] += float(event.quantity_base or 0)
    return VolumeProfile(weights=tuple(buckets), source=source, n_sessions=n_sessions)


def merge_profiles(profiles: Sequence[VolumeProfile], source: str) -> VolumeProfile:
    """Average several session profiles into one (equal weight per session)."""
    if not profiles:
        raise LeakageError(
            "no prior-session volume profiles available; refusing to schedule VWAP "
            "without a historical profile"
        )
    n_buckets = min(p.n_buckets for p in profiles)
    totals = [0.0] * n_buckets
    for profile in profiles:
        session_total = sum(profile.weights[:n_buckets]) or 1.0
        for i in range(n_buckets):
            totals[i] += profile.weights[i] / session_total
    count = len(profiles)
    return VolumeProfile(weights=tuple(t / count for t in totals), source=source, n_sessions=count)


def flat_profile(n_buckets: int, source: str = "flat_fallback") -> VolumeProfile:
    """Uniform profile. Explicitly a fallback, and labelled as such."""
    return VolumeProfile(weights=tuple(1.0 / n_buckets for _ in range(n_buckets)), source=source)
