"""Post-fill markouts: was the fill adversely selected?

A markout measures where the mid went *after* our fill. Sign convention is
system-wide: **positive = adverse**.

    BUY  filled at p, mid later m  ->  adverse when m < p
    SELL filled at p, mid later m  ->  adverse when m > p

Passive execution is the natural home of adverse selection: you get filled
precisely when the market is about to move against you. Reporting markouts at
several horizons is what makes that visible instead of hiding it behind an
average fill price.

Fills whose horizon extends past the recorded data are counted as *not
measurable* and excluded. They are never imputed as zero - a missing markout
and a flat markout are different facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from ..domain.fills import Fill, markout_bps
from .observer import MarketObserver

DEFAULT_HORIZONS_NS: tuple[int, ...] = (
    1_000_000_000,
    10_000_000_000,
    60_000_000_000,
    300_000_000_000,
)


@dataclass(frozen=True, slots=True)
class MarkoutSummary:
    """Markout distribution for one horizon, across all fills."""

    horizon_ns: int
    n_fills: int
    n_measurable: int
    volume_weighted_bps: float | None
    mean_bps: float | None
    median_bps: float | None
    p25_bps: float | None
    p75_bps: float | None
    worst_bps: float | None
    best_bps: float | None

    @property
    def coverage(self) -> float:
        return 0.0 if self.n_fills == 0 else self.n_measurable / self.n_fills

    def to_dict(self) -> dict[str, object]:
        return {
            "horizon_ns": self.horizon_ns,
            "horizon_s": self.horizon_ns / 1e9,
            "n_fills": self.n_fills,
            "n_measurable": self.n_measurable,
            "coverage": self.coverage,
            "volume_weighted_bps": self.volume_weighted_bps,
            "mean_bps": self.mean_bps,
            "median_bps": self.median_bps,
            "p25_bps": self.p25_bps,
            "p75_bps": self.p75_bps,
            "worst_bps": self.worst_bps,
            "best_bps": self.best_bps,
        }


def _percentile(ordered: list[float], fraction: float) -> float:
    if not ordered:
        raise ValueError("empty sample")
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def compute_markouts(
    fills: tuple[Fill, ...],
    observer: MarketObserver,
    horizons_ns: tuple[int, ...] = DEFAULT_HORIZONS_NS,
) -> tuple[MarkoutSummary, ...]:
    """One summary per horizon. Horizons with no measurable fill yield None."""
    return tuple(_summarise(fills, observer, h) for h in horizons_ns)


def _summarise(
    fills: tuple[Fill, ...], observer: MarketObserver, horizon_ns: int
) -> MarkoutSummary:
    values: list[float] = []
    weights: list[int] = []
    for fill in fills:
        if fill.quantity_base <= 0:
            continue
        future_mid = observer.mid_at_or_after(fill.timestamp_ns + horizon_ns)
        if future_mid is None:
            continue
        values.append(markout_bps(fill.side, float(fill.price_ticks), future_mid))
        weights.append(fill.quantity_base)

    if not values:
        return MarkoutSummary(
            horizon_ns=horizon_ns,
            n_fills=len(fills),
            n_measurable=0,
            volume_weighted_bps=None,
            mean_bps=None,
            median_bps=None,
            p25_bps=None,
            p75_bps=None,
            worst_bps=None,
            best_bps=None,
        )

    ordered = sorted(values)
    total_weight = sum(weights)
    weighted = (
        sum(v * w for v, w in zip(values, weights, strict=True)) / total_weight
        if total_weight > 0
        else None
    )
    return MarkoutSummary(
        horizon_ns=horizon_ns,
        n_fills=len(fills),
        n_measurable=len(values),
        volume_weighted_bps=weighted,
        mean_bps=sum(values) / len(values),
        median_bps=median(ordered),
        p25_bps=_percentile(ordered, 0.25),
        p75_bps=_percentile(ordered, 0.75),
        worst_bps=ordered[-1],
        best_bps=ordered[0],
    )
