"""VWAP: schedule proportional to a HISTORICAL volume profile.

The profile must come from prior sessions (`profile_source: historical`) or a
causal real-time estimator. Scheduling with the current session's realized
volume curve is future leakage and is rejected.

If no prior-session profile is supplied the policy refuses to guess: it raises,
because a silently uniform "VWAP" would be a false claim.
"""

from __future__ import annotations

from ...domain.exceptions import LeakageError
from ..volume_profile import VolumeProfile
from .base import Schedule, ScheduledPolicy


class VwapPolicy(ScheduledPolicy):
    def __init__(self, *, profile: VolumeProfile | None = None, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        # Fail at construction, not at the first slice. A missing profile is a
        # configuration error, and discovering it part-way through a replay
        # wastes the whole run - and, worse, looks like a market event.
        if profile is None:
            raise LeakageError(
                "VWAP policy needs a historical volume profile; none was provided. "
                "Provide prior-session profiles instead of using the current session."
            )
        self._profile: VolumeProfile = profile

    @property
    def name(self) -> str:
        return "vwap"

    @property
    def profile(self) -> VolumeProfile:
        return self._profile

    def build_schedule(self) -> Schedule:
        weights = list(self.profile.for_slices(self._n_slices))
        return Schedule(
            slice_times_ns=self._slice_times(),
            cumulative_targets=self._cumulative_from_weights(weights),
        )

    def describe(self) -> dict[str, object]:
        return {
            "algorithm": self.name,
            "profile_source": self._profile.source,
            "n_sessions": self._profile.n_sessions,
            "profile_caveat": (
                "the schedule is proportional to a PRIOR-session volume profile; "
                "the current session's realized curve is never used"
            ),
        }
