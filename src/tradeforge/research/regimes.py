"""Market regime classification.

Regimes are defined by **absolute thresholds from configuration**, never by
quantiles of the sample being studied. That distinction matters: classifying a
session into "high volatility" using its own volatility distribution is a form
of look-ahead - the threshold would have been unknowable at the time, and it
guarantees that every session contains exactly the same number of "high vol"
observations regardless of whether it was calm or violent.

A state can carry several labels at once (wide spread AND thin depth AND
imbalanced), which is the honest representation: regimes overlap.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..domain.book import MarketState


class RegimeLabel(StrEnum):
    TIGHT_SPREAD = "tight_spread"
    WIDE_SPREAD = "wide_spread"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    HIGH_DEPTH = "high_depth"
    LOW_DEPTH = "low_depth"
    BALANCED_BOOK = "balanced_book"
    IMBALANCED_BOOK = "imbalanced_book"


@dataclass(frozen=True, slots=True)
class RegimeThresholds:
    """Absolute cut-offs. Every one of these comes from config, not from data."""

    spread_tight_max_ticks: int = 1
    spread_wide_min_ticks: int = 3
    volatility_high_min_bps: float = 5.0
    volatility_low_max_bps: float = 1.0
    depth_high_min_base: int = 5_000
    depth_low_max_base: int = 1_500
    imbalance_abs_high: float = 0.5
    depth_levels: int = 5

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> RegimeThresholds:
        regimes = payload.get("regimes", {}) if isinstance(payload, dict) else {}
        if not isinstance(regimes, dict):
            raise TypeError("research config must contain a 'regimes' mapping")
        return cls(
            spread_tight_max_ticks=int(str(regimes.get("spread_tight_max_ticks", 1))),
            spread_wide_min_ticks=int(str(regimes.get("spread_wide_min_ticks", 3))),
            volatility_high_min_bps=float(str(regimes.get("volatility_high_min_bps", 5.0))),
            volatility_low_max_bps=float(str(regimes.get("volatility_low_max_bps", 1.0))),
            depth_high_min_base=int(str(regimes.get("depth_high_min_base", 5_000))),
            depth_low_max_base=int(str(regimes.get("depth_low_max_base", 1_500))),
            imbalance_abs_high=float(str(regimes.get("imbalance_abs_high", 0.5))),
            depth_levels=int(str(regimes.get("depth_levels", 5))),
        )


def classify_state(
    state: MarketState,
    thresholds: RegimeThresholds,
    *,
    realized_vol_bps: float | None = None,
) -> frozenset[RegimeLabel]:
    """Labels for one instant. Empty set when the state is unclassifiable.

    `realized_vol_bps` is supplied by the caller from the feature engine: it is
    a past-window estimate, so passing it in keeps this function pure and makes
    the causality explicit.
    """
    labels: set[RegimeLabel] = set()
    spread = state.spread_ticks
    if spread is not None:
        if spread <= thresholds.spread_tight_max_ticks:
            labels.add(RegimeLabel.TIGHT_SPREAD)
        if spread >= thresholds.spread_wide_min_ticks:
            labels.add(RegimeLabel.WIDE_SPREAD)

    if realized_vol_bps is not None:
        if realized_vol_bps >= thresholds.volatility_high_min_bps:
            labels.add(RegimeLabel.HIGH_VOLATILITY)
        if realized_vol_bps <= thresholds.volatility_low_max_bps:
            labels.add(RegimeLabel.LOW_VOLATILITY)

    # `state.bid_levels and sum(...)` was a short-circuit guard that returned the
    # empty tuple rather than 0 when there were no bids, so a one-sided book hit
    # `tuple + int` and raised TypeError instead of being classified. Sum each
    # side unconditionally; the two-sided tests below handle the rest.
    depth = sum(level.quantity_base for level in state.bid_levels[: thresholds.depth_levels])
    ask_depth = sum(level.quantity_base for level in state.ask_levels[: thresholds.depth_levels])
    two_sided = depth + ask_depth
    if two_sided >= thresholds.depth_high_min_base:
        labels.add(RegimeLabel.HIGH_DEPTH)
    if 0 < two_sided <= thresholds.depth_low_max_base:
        labels.add(RegimeLabel.LOW_DEPTH)
    # Imbalance needs both sides to be observable. With one side empty the ratio
    # would be ±1 by construction, which says nothing about the book.
    if depth > 0 and ask_depth > 0:
        imbalance = (depth - ask_depth) / two_sided
        if abs(imbalance) >= thresholds.imbalance_abs_high:
            labels.add(RegimeLabel.IMBALANCED_BOOK)
        else:
            labels.add(RegimeLabel.BALANCED_BOOK)

    return frozenset(labels)


@dataclass
class RegimeTally:
    """Counts how often each label occurred, and how long in total."""

    n_states: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    duration_ns: dict[str, int] = field(default_factory=dict)
    last_timestamp_ns: int | None = None

    def record(self, labels: frozenset[RegimeLabel], timestamp_ns: int) -> None:
        self.n_states += 1
        elapsed = 0 if self.last_timestamp_ns is None else timestamp_ns - self.last_timestamp_ns
        self.last_timestamp_ns = timestamp_ns
        for label in labels:
            self.counts[label.value] = self.counts.get(label.value, 0) + 1
            self.duration_ns[label.value] = self.duration_ns.get(label.value, 0) + max(elapsed, 0)

    def share(self, label: RegimeLabel) -> float:
        if self.n_states == 0:
            return 0.0
        return self.counts.get(label.value, 0) / self.n_states

    def to_dict(self) -> dict[str, object]:
        return {
            "n_states": self.n_states,
            "counts": dict(sorted(self.counts.items())),
            "duration_s": {key: value / 1e9 for key, value in sorted(self.duration_ns.items())},
            "shares": {key: value / self.n_states for key, value in sorted(self.counts.items())}
            if self.n_states
            else {},
        }
