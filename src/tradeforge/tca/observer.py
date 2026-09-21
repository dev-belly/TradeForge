"""Market observation for TCA.

This module deliberately looks forward in time. Markouts and interval
benchmarks are *post-trade measurements*: they ask "what did the market do
after we traded", which cannot be answered without future data.

Two rules keep that safe:

  1. Nothing in this module is ever reachable from an execution policy. The
     policy only receives `MarketState`, which has no handle to an observer.
  2. Every value carries the observation count it was computed from, so a
     benchmark backed by three prints is visibly weaker than one backed by
     three thousand.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from ..domain.book import MarketState
from ..domain.enums import EventType, Side
from ..domain.events import MarketEvent


@dataclass(frozen=True, slots=True)
class MidPoint:
    timestamp_ns: int
    mid_ticks: float


@dataclass(frozen=True, slots=True)
class TradePrint:
    timestamp_ns: int
    price_ticks: int
    quantity_base: int
    aggressor_side: Side | None


class MarketObserver:
    """Records mids and trade prints over an explicit time range.

    `start_ns`/`end_ns` bound the *benchmark window*. The observer keeps
    accepting events past `end_ns` only if the caller widens the range, which is
    how markout horizons are covered.
    """

    def __init__(self, start_ns: int, end_ns: int) -> None:
        if end_ns < start_ns:
            raise ValueError(f"end_ns {end_ns} precedes start_ns {start_ns}")
        self._start_ns = start_ns
        self._end_ns = end_ns
        self._mids: list[MidPoint] = []
        self._mid_times: list[int] = []
        self._trades: list[TradePrint] = []
        self._n_states = 0

    # ------------------------------------------------------------ properties

    @property
    def start_ns(self) -> int:
        return self._start_ns

    @property
    def end_ns(self) -> int:
        return self._end_ns

    @property
    def mids(self) -> tuple[MidPoint, ...]:
        return tuple(self._mids)

    @property
    def trades(self) -> tuple[TradePrint, ...]:
        return tuple(self._trades)

    @property
    def n_states(self) -> int:
        return self._n_states

    # -------------------------------------------------------------- ingestion

    def observe(self, event: MarketEvent, state: MarketState) -> None:
        ts = event.exchange_timestamp_ns
        if not self._start_ns <= ts <= self._end_ns:
            return
        self._n_states += 1
        if state.has_two_sided_book:
            mid = state.mid_ticks
            if mid is not None:
                self._mids.append(MidPoint(timestamp_ns=ts, mid_ticks=mid))
                self._mid_times.append(ts)
        if event.event_type is EventType.TRADE and event.price_ticks and event.quantity_base:
            self._trades.append(
                TradePrint(
                    timestamp_ns=ts,
                    price_ticks=event.price_ticks,
                    quantity_base=event.quantity_base,
                    aggressor_side=event.aggressor_side() or event.side,
                )
            )

    # ---------------------------------------------------------------- lookups

    def mid_at_or_before(self, timestamp_ns: int) -> float | None:
        """Last observed mid at or before `timestamp_ns` (None if none yet)."""
        index = bisect_right(self._mid_times, timestamp_ns)
        return None if index == 0 else self._mids[index - 1].mid_ticks

    def mid_strictly_before(self, timestamp_ns: int) -> float | None:
        """Last observed mid strictly before `timestamp_ns`.

        Used as the reference for effective-spread calculations, so the mid of
        the very event that produced our fill cannot leak into its own cost.
        """
        index = bisect_left(self._mid_times, timestamp_ns)
        return None if index == 0 else self._mids[index - 1].mid_ticks

    def mid_at_or_after(self, timestamp_ns: int) -> float | None:
        """First observed mid at or after `timestamp_ns` (None if out of range).

        Returns None rather than extrapolating: a markout horizon the data does
        not reach must be reported as missing, never as zero.
        """
        index = bisect_left(self._mid_times, timestamp_ns)
        return None if index >= len(self._mids) else self._mids[index].mid_ticks

    def first_mid(self) -> float | None:
        return self._mids[0].mid_ticks if self._mids else None

    def last_mid(self) -> float | None:
        return self._mids[-1].mid_ticks if self._mids else None

    def time_weighted_mid(self) -> float | None:
        """Trapezoidal time-weighted average mid. The honest TWAP proxy.

        A plain average of observations would over-weight busy periods, because
        more events arrive when the book is active.
        """
        if len(self._mids) < 2:
            return self._mids[0].mid_ticks if self._mids else None
        total = 0.0
        for left, right in zip(self._mids, self._mids[1:], strict=False):
            span = right.timestamp_ns - left.timestamp_ns
            if span <= 0:
                continue
            total += 0.5 * (left.mid_ticks + right.mid_ticks) * span
        duration = self._mid_times[-1] - self._mid_times[0]
        if duration <= 0:
            return self._mids[0].mid_ticks
        return total / duration

    def volume_weighted_price(self) -> float | None:
        """Interval VWAP from trade prints inside the window."""
        total_qty = sum(t.quantity_base for t in self._trades)
        if total_qty <= 0:
            return None
        notional = sum(t.price_ticks * t.quantity_base for t in self._trades)
        return notional / total_qty

    def mean_mid(self) -> float | None:
        """Unweighted mean of observed mids, reported as the crudest benchmark."""
        if not self._mids:
            return None
        return sum(m.mid_ticks for m in self._mids) / len(self._mids)

    def total_traded_volume(self) -> int:
        return sum(t.quantity_base for t in self._trades)
