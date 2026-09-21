"""Causal microstructure feature engine.

Every feature is computed only from information available at the observation
time. There is no `fit` on the full day, no forward-looking rolling window, and
no access to the event stream - the engine is fed events one at a time.

Feature documentation lives in `docs/microstructure.md`.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, fields

from ..domain.book import BookSnapshot, MarketState, TradeRecord
from ..domain.enums import EventType, Side
from ..domain.events import MarketEvent
from ..orderbook import (
    depth_slope,
    microprice_ticks,
    multi_level_imbalance,
    quoted_spread_ticks,
    relative_spread_bps,
    top_imbalance,
)
from .ofi import OrderFlowImbalance
from .volatility import RealizedVolatility


@dataclass(frozen=True, slots=True)
class FeatureSet:
    """Point-in-time feature vector. All fields are past-observable."""

    timestamp_ns: int
    best_bid_ticks: int | None
    best_ask_ticks: int | None
    mid_ticks: float | None
    microprice_ticks: float | None
    spread_ticks: int | None
    relative_spread_bps: float | None
    top_imbalance: float | None
    multilevel_imbalance: float | None
    bid_depth_base: int
    ask_depth_base: int
    depth_slope_bid: float | None
    depth_slope_ask: float | None
    ofi_rolling: float
    trade_imbalance: float | None
    signed_volume_base: int
    recent_volume_base: int
    trade_count: int
    add_count: int
    cancel_count: int
    realized_vol_bps: float
    trade_intensity_per_s: float
    cancel_intensity_per_s: float

    def as_dict(self) -> dict[str, float | int | None]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def numeric_fields(cls) -> tuple[str, ...]:
        return tuple(f.name for f in fields(cls) if f.name != "timestamp_ns")


class MicrostructureEngine:
    """Stateful, causal feature computer."""

    def __init__(self, window_ns: int = 60_000_000_000, depth_levels: int = 5) -> None:
        self._window_ns = window_ns
        self._depth_levels = depth_levels
        self._ofi = OrderFlowImbalance(window_ns=window_ns)
        self._vol = RealizedVolatility(window_ns=window_ns)
        self._trades: deque[TradeRecord] = deque()
        self._cumulative_volume_base = 0
        # Rolling buy/sell volume maintained incrementally. Re-summing the
        # deque on every event is O(window) per event and dominated the profile;
        # these counters make it O(1) while producing identical values.
        self._window_buy_volume = 0
        self._window_sell_volume = 0
        self._add_count = 0
        self._cancel_count = 0
        self._window_adds: deque[int] = deque()
        self._window_cancels: deque[int] = deque()
        self._last_snapshot: BookSnapshot | None = None

    @property
    def window_ns(self) -> int:
        return self._window_ns

    def on_event(self, event: MarketEvent, snapshot: BookSnapshot) -> None:
        """Update rolling state. Call *after* the book has applied the event."""
        ts = event.exchange_timestamp_ns
        self._last_snapshot = snapshot
        self._ofi.update(
            timestamp_ns=ts,
            bid_price=snapshot.best_bid_ticks,
            bid_qty=snapshot.best_bid_qty_base,
            ask_price=snapshot.best_ask_ticks,
            ask_qty=snapshot.best_ask_qty_base,
        )
        self._vol.update(timestamp_ns=ts, mid_ticks=snapshot.mid_ticks)
        if event.event_type is EventType.TRADE:
            aggressor = event.aggressor_side() or event.side
            quantity = event.quantity_base or 0
            self._cumulative_volume_base += quantity
            # Trades with an unknown aggressor are excluded from both sides, so
            # the signed imbalance never silently treats them as neutral.
            if aggressor is Side.BUY:
                self._window_buy_volume += quantity
            elif aggressor is Side.SELL:
                self._window_sell_volume += quantity
            self._trades.append(
                TradeRecord(
                    timestamp_ns=ts,
                    price_ticks=event.price_ticks or 0,
                    quantity_base=quantity,
                    aggressor_side=aggressor,
                )
            )
        elif event.event_type is EventType.ADD:
            self._add_count += 1
            self._window_adds.append(ts)
        elif event.event_type is EventType.CANCEL:
            self._cancel_count += 1
            self._window_cancels.append(ts)
        self._evict(ts)

    def observe(self, snapshot: BookSnapshot | None = None) -> FeatureSet:
        """Compute the feature vector for the current instant."""
        snap = snapshot or self._last_snapshot
        if snap is None:
            raise ValueError("no snapshot available; call on_event first")
        ts = snap.timestamp_ns
        window_s = self._window_ns / 1e9
        buy_volume = self._window_buy_volume
        sell_volume = self._window_sell_volume
        total_volume = buy_volume + sell_volume
        return FeatureSet(
            timestamp_ns=ts,
            best_bid_ticks=snap.best_bid_ticks,
            best_ask_ticks=snap.best_ask_ticks,
            mid_ticks=snap.mid_ticks,
            microprice_ticks=microprice_ticks(snap),
            spread_ticks=quoted_spread_ticks(snap),
            relative_spread_bps=relative_spread_bps(snap),
            top_imbalance=top_imbalance(snap),
            multilevel_imbalance=multi_level_imbalance(snap, self._depth_levels),
            bid_depth_base=snap.depth_base(Side.BUY, self._depth_levels),
            ask_depth_base=snap.depth_base(Side.SELL, self._depth_levels),
            depth_slope_bid=depth_slope(snap, Side.BUY, self._depth_levels),
            depth_slope_ask=depth_slope(snap, Side.SELL, self._depth_levels),
            ofi_rolling=self._ofi.rolling_ofi(ts),
            trade_imbalance=((buy_volume - sell_volume) / total_volume if total_volume else None),
            signed_volume_base=buy_volume - sell_volume,
            recent_volume_base=total_volume,
            trade_count=len(self._trades),
            add_count=len(self._window_adds),
            cancel_count=len(self._window_cancels),
            realized_vol_bps=self._vol.volatility_bps(ts),
            trade_intensity_per_s=len(self._trades) / window_s if window_s else 0.0,
            cancel_intensity_per_s=len(self._window_cancels) / window_s if window_s else 0.0,
        )

    def to_market_state(self, snapshot: BookSnapshot, halted: bool = False) -> MarketState:
        """Build the value object a policy is allowed to see."""
        buy_volume = self._window_buy_volume
        sell_volume = self._window_sell_volume
        return MarketState(
            symbol=snapshot.symbol,
            timestamp_ns=snapshot.timestamp_ns,
            sequence_id=snapshot.sequence_id,
            best_bid_ticks=snapshot.best_bid_ticks,
            best_ask_ticks=snapshot.best_ask_ticks,
            best_bid_qty_base=snapshot.best_bid_qty_base,
            best_ask_qty_base=snapshot.best_ask_qty_base,
            bid_levels=snapshot.bids,
            ask_levels=snapshot.asks,
            last_trade_ticks=self._trades[-1].price_ticks if self._trades else None,
            last_trade_qty_base=self._trades[-1].quantity_base if self._trades else 0,
            aggressor_side_known=bool(self._trades) and self._trades[-1].aggressor_side is not None,
            market_volume_base=self._total_market_volume(),
            recent_volume_base=buy_volume + sell_volume,
            recent_trade_count=len(self._trades),
            recent_buy_volume_base=buy_volume,
            recent_sell_volume_base=sell_volume,
            book_updates=self._add_count + self._cancel_count,
            halted=halted,
        )

    def _total_market_volume(self) -> int:
        """Cumulative traded volume since the start of the stream.

        Deliberately NOT the rolling-window total. POV compares consecutive
        readings to get interval volume, and a rolling value makes that
        difference meaningless - it shrinks as old trades age out, so the delta
        can even go negative and the policy silently stops trading.
        The rolling measure is exposed separately as `recent_volume_base`.
        """
        return self._cumulative_volume_base

    def _evict(self, timestamp_ns: int) -> None:
        cutoff = timestamp_ns - self._window_ns
        while self._trades and self._trades[0].timestamp_ns < cutoff:
            expired = self._trades.popleft()
            if expired.aggressor_side is Side.BUY:
                self._window_buy_volume -= expired.quantity_base
            elif expired.aggressor_side is Side.SELL:
                self._window_sell_volume -= expired.quantity_base
        while self._window_adds and self._window_adds[0] < cutoff:
            self._window_adds.popleft()
        while self._window_cancels and self._window_cancels[0] < cutoff:
            self._window_cancels.popleft()
