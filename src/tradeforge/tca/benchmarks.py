"""Execution benchmarks.

Every benchmark here is computed from the market *inside the execution window*.
That is legitimate for **evaluation** and forbidden for **scheduling**: a VWAP
policy that scheduled against this same-window curve would be reading the
future (see `execution/volume_profile.py`, which refuses to build a profile from
the current session).

The distinction is the whole reason `BenchmarkKind` and `VolumeProfile` are
separate types with separate constructors.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.enums import BenchmarkKind
from .observer import MarketObserver


@dataclass(frozen=True, slots=True)
class BenchmarkPrices:
    """Window benchmarks, in ticks. `None` means "not measurable from this data"."""

    window_start_ns: int
    window_end_ns: int
    arrival_mid_ticks: float | None
    interval_vwap_ticks: float | None
    interval_twap_ticks: float | None
    interval_mid_ticks: float | None
    terminal_mid_ticks: float | None
    n_mid_observations: int
    n_trade_prints: int
    window_volume_base: int

    @property
    def window_ns(self) -> int:
        return self.window_end_ns - self.window_start_ns

    def value(self, kind: BenchmarkKind) -> float | None:
        return {
            BenchmarkKind.ARRIVAL_PRICE: self.arrival_mid_ticks,
            BenchmarkKind.INTERVAL_VWAP: self.interval_vwap_ticks,
            BenchmarkKind.INTERVAL_TWAP: self.interval_twap_ticks,
            BenchmarkKind.INTERVAL_MID: self.interval_mid_ticks,
            BenchmarkKind.CLOSE: self.terminal_mid_ticks,
        }[kind]

    def coverage(self) -> dict[str, object]:
        """How well each benchmark is backed by observations."""
        return {
            "n_mid_observations": self.n_mid_observations,
            "n_trade_prints": self.n_trade_prints,
            "window_volume_base": self.window_volume_base,
            "vwap_available": self.interval_vwap_ticks is not None,
            "twap_available": self.interval_twap_ticks is not None,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "window_start_ns": self.window_start_ns,
            "window_end_ns": self.window_end_ns,
            "window_ns": self.window_ns,
            "arrival_mid_ticks": self.arrival_mid_ticks,
            "interval_vwap_ticks": self.interval_vwap_ticks,
            "interval_twap_ticks": self.interval_twap_ticks,
            "interval_mid_ticks": self.interval_mid_ticks,
            "terminal_mid_ticks": self.terminal_mid_ticks,
            **self.coverage(),
        }


def compute_benchmarks(observer: MarketObserver, *, start_ns: int, end_ns: int) -> BenchmarkPrices:
    """Build every window benchmark from one observer.

    `arrival_mid_ticks` is the first mid observed at or after the window opens.
    Using the first observation *after* the start rather than the closest one
    keeps it causal: it is a price we could actually have seen before deciding.
    """
    arrival = observer.mid_at_or_after(start_ns)
    return BenchmarkPrices(
        window_start_ns=start_ns,
        window_end_ns=end_ns,
        arrival_mid_ticks=arrival,
        interval_vwap_ticks=observer.volume_weighted_price(),
        interval_twap_ticks=observer.time_weighted_mid(),
        interval_mid_ticks=observer.mean_mid(),
        terminal_mid_ticks=observer.mid_at_or_before(end_ns),
        n_mid_observations=len(observer.mids),
        n_trade_prints=len(observer.trades),
        window_volume_base=observer.total_traded_volume(),
    )
