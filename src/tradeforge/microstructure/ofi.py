"""Order Flow Imbalance (OFI).

Definition used here (best-level OFI, Cont, Cucuringu & Zhang 2023, eq. 2;
also the same construction as Cont, Stoikov & Talreja 2010 restricted to the
touch):

    e_n = I{P_B(n) >= P_B(n-1)} * q_B(n) - I{P_B(n) <= P_B(n-1)} * q_B(n-1)
        - I{P_A(n) <= P_A(n-1)} * q_A(n) + I{P_A(n) >= P_A(n-1)} * q_A(n-1)

where P_B/q_B are the best bid price/size and P_A/q_A the best ask price/size.

Deliberately NOT `buy_volume - sell_volume`: that is *trade* imbalance, a
different quantity. We compute both and keep the names distinct.

Requirements: best price and size on both sides (L1). The multi-level variant
needs L2 and is exposed separately.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OfiSample:
    timestamp_ns: int
    ofi: float


class OrderFlowImbalance:
    """Event-level OFI with a causal rolling sum.

    Only information available at the time of the event is used: the accumulator
    never looks at a later event.
    """

    def __init__(self, window_ns: int = 60_000_000_000) -> None:
        self._window_ns = window_ns
        self._samples: deque[OfiSample] = deque()
        self._prev_bid_price: int | None = None
        self._prev_ask_price: int | None = None
        self._prev_bid_qty: int = 0
        self._prev_ask_qty: int = 0

    @property
    def window_ns(self) -> int:
        return self._window_ns

    def update(
        self,
        *,
        timestamp_ns: int,
        bid_price: int | None,
        bid_qty: int,
        ask_price: int | None,
        ask_qty: int,
    ) -> float:
        """Feed the book state after an event; returns this event's OFI."""
        if bid_price is None or ask_price is None:
            self._evict(timestamp_ns)
            return 0.0
        if self._prev_bid_price is None or self._prev_ask_price is None:
            self._prev_bid_price, self._prev_bid_qty = bid_price, bid_qty
            self._prev_ask_price, self._prev_ask_qty = ask_price, ask_qty
            return 0.0

        bid_term = 0.0
        if bid_price >= self._prev_bid_price:
            bid_term += bid_qty
        if bid_price <= self._prev_bid_price:
            bid_term -= self._prev_bid_qty

        ask_term = 0.0
        if ask_price <= self._prev_ask_price:
            ask_term += ask_qty
        if ask_price >= self._prev_ask_price:
            ask_term -= self._prev_ask_qty

        ofi = float(bid_term - ask_term)
        self._samples.append(OfiSample(timestamp_ns=timestamp_ns, ofi=ofi))
        self._prev_bid_price, self._prev_bid_qty = bid_price, bid_qty
        self._prev_ask_price, self._prev_ask_qty = ask_price, ask_qty
        self._evict(timestamp_ns)
        return ofi

    def rolling_ofi(self, timestamp_ns: int | None = None) -> float:
        if timestamp_ns is not None:
            self._evict(timestamp_ns)
        return sum(sample.ofi for sample in self._samples)

    def n_samples(self) -> int:
        return len(self._samples)

    def _evict(self, timestamp_ns: int) -> None:
        cutoff = timestamp_ns - self._window_ns
        while self._samples and self._samples[0].timestamp_ns < cutoff:
            self._samples.popleft()
