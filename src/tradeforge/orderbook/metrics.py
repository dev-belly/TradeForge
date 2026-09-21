"""Derived book metrics.

Pure functions over an immutable `BookSnapshot`. Nothing here mutates the book,
and nothing here uses data from the future: a snapshot is a point-in-time view.

Conventions:
  * tick-based inputs, float outputs where an average is involved;
  * returns `None` when the book is one-sided (never a silent 0.0).
"""

from __future__ import annotations

from ..domain.book import BookSnapshot, PriceLevel
from ..domain.enums import Side


def quoted_spread_ticks(snapshot: BookSnapshot) -> int | None:
    """Quoted spread = best_ask - best_bid (ticks).

    Intuition: the cost of a round trip for a liquidity taker; the reward for a
    market maker. Required data: L1.
    """
    return snapshot.spread_ticks


def relative_spread_bps(snapshot: BookSnapshot) -> float | None:
    """Spread normalized by the mid, in basis points. Comparable across symbols."""
    mid = snapshot.mid_ticks
    spread = snapshot.spread_ticks
    if mid is None or spread is None or mid == 0:
        return None
    return spread / mid * 10_000.0


def mid_ticks(snapshot: BookSnapshot) -> float | None:
    """Mid price in ticks. Ignores depth by construction."""
    return snapshot.mid_ticks


def microprice_ticks(snapshot: BookSnapshot) -> float | None:
    """Depth-weighted mid: (P_bid*Q_ask + P_ask*Q_bid) / (Q_bid + Q_ask).

    Intuition: if the ask is thin, the next price move is more likely up, so the
    "fair" price sits above the mid. Required data: L1 (top sizes).

    Failure mode: with equal sizes it collapses to the mid; it is a
    *heuristic*, not a forecast - TradeForge never claims otherwise.
    """
    if not snapshot.bids or not snapshot.asks:
        return None
    bid_px = snapshot.bids[0].price_ticks
    ask_px = snapshot.asks[0].price_ticks
    bid_qty = snapshot.bids[0].quantity_base
    ask_qty = snapshot.asks[0].quantity_base
    total = bid_qty + ask_qty
    if total == 0:
        return None
    return (bid_px * ask_qty + ask_px * bid_qty) / total


def top_imbalance(snapshot: BookSnapshot) -> float | None:
    """(Q_bid - Q_ask) / (Q_bid + Q_ask), in [-1, 1].

    Positive = more resting bid liquidity = upward pressure.
    """
    if not snapshot.bids or not snapshot.asks:
        return None
    bid_qty = snapshot.bids[0].quantity_base
    ask_qty = snapshot.asks[0].quantity_base
    total = bid_qty + ask_qty
    if total == 0:
        return None
    return (bid_qty - ask_qty) / total


def multi_level_imbalance(snapshot: BookSnapshot, levels: int = 5) -> float | None:
    """Same as `top_imbalance` over the top `levels` levels per side. L2 required."""
    bid_qty = _sum_qty(snapshot.bids, levels)
    ask_qty = _sum_qty(snapshot.asks, levels)
    total = bid_qty + ask_qty
    if total == 0:
        return None
    return (bid_qty - ask_qty) / total


def depth_base(snapshot: BookSnapshot, side: Side, levels: int | None = None) -> int:
    """Resting quantity available on one side."""
    return snapshot.depth_base(side, levels)


def depth_slope(snapshot: BookSnapshot, side: Side, levels: int = 5) -> float | None:
    """Least-squares slope of cumulative depth versus distance from the touch.

    Definition: regress cumulative quantity (y) on distance in ticks from the
    best price (x) over the top `levels`, giving shares per tick.

    Intuition: a steep book means liquidity returns quickly behind the touch -
    impact decays fast. A flat book means you must walk far to get size.
    Required data: L2 (MBP).
    """
    book = snapshot.bids if side is Side.BUY else snapshot.asks
    if len(book) < 2:
        return None
    points = book[:levels]
    xs: list[float] = []
    ys: list[float] = []
    cumulative = 0
    for level in points:
        distance = abs(level.price_ticks - points[0].price_ticks)
        cumulative += level.quantity_base
        xs.append(float(distance))
        ys.append(float(cumulative))
    return _least_squares_slope(xs, ys)


def price_levels(snapshot: BookSnapshot, side: Side, levels: int) -> tuple[PriceLevel, ...]:
    return snapshot.levels(side, levels)


def _sum_qty(levels: tuple[PriceLevel, ...], n: int) -> int:
    return sum(level.quantity_base for level in levels[:n])


def _least_squares_slope(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return 0.0
    return num / den
