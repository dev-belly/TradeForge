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
from itertools import pairwise

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

    The observer may cover later events for markouts. Only observations inside
    this execution window may contribute to its price or coverage benchmarks.
    """
    if end_ns < start_ns:
        raise ValueError("benchmark end_ns precedes start_ns")
    mids = [m for m in observer.mids if start_ns <= m.timestamp_ns <= end_ns]
    trades = [t for t in observer.trades if start_ns <= t.timestamp_ns <= end_ns]
    volume = sum(t.quantity_base for t in trades)
    twap: float | None = None
    if len(mids) >= 2:
        duration = mids[-1].timestamp_ns - mids[0].timestamp_ns
        if duration > 0:
            weighted = sum(
                (left.mid_ticks + right.mid_ticks) * 0.5 * (right.timestamp_ns - left.timestamp_ns)
                for left, right in pairwise(mids)
            )
            twap = weighted / duration
    return BenchmarkPrices(
        window_start_ns=start_ns,
        window_end_ns=end_ns,
        arrival_mid_ticks=mids[0].mid_ticks if mids else None,
        interval_vwap_ticks=(
            sum(t.price_ticks * t.quantity_base for t in trades) / volume if volume else None
        ),
        interval_twap_ticks=twap,
        interval_mid_ticks=sum(m.mid_ticks for m in mids) / len(mids) if mids else None,
        terminal_mid_ticks=mids[-1].mid_ticks if mids else None,
        n_mid_observations=len(mids),
        n_trade_prints=len(trades),
        window_volume_base=volume,
    )
