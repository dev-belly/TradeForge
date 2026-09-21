"""Reconstruct a book from an event stream and look at it.

Shows the core loop and three things worth noticing:

  * the book is built from events only - there is no "set the book" API;
  * a `MarketState` is a value object, so a policy cannot mutate the book;
  * a trade consumes the *resting* side at the print price.

Run: python examples/01_reconstruct_the_book.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tradeforge.data.registry import create_adapter
from tradeforge.domain.enums import Side
from tradeforge.microstructure import MicrostructureEngine
from tradeforge.orderbook import (
    BookSettings,
    create_book,
    microprice_ticks,
    relative_spread_bps,
    top_imbalance,
)


def main() -> int:
    adapter = create_adapter(
        "synthetic",
        {"seed": 20260908, "n_events": 3_000, "start_time_ns": 34_200_000_000_000},
    )
    print(f"adapter: {adapter.name}")
    print(f"declared data type: {adapter.data_type.value}")
    print()

    book = create_book(
        symbol="SYNTH",
        settings=BookSettings.from_dict({}),
        data_type=adapter.data_type,
    )
    engine = MicrostructureEngine()

    for index, event in enumerate(adapter.events()):
        book.apply(event)
        snapshot = book.snapshot(10)
        engine.on_event(event, snapshot)

        if index in (5, 500, 1_500):
            state = engine.to_market_state(snapshot)
            print(f"--- after {index + 1} events ---")
            print(f"  best bid      {snapshot.best_bid_ticks} x {snapshot.best_bid_qty_base}")
            print(f"  best ask      {snapshot.best_ask_ticks} x {snapshot.best_ask_qty_base}")
            print(
                f"  spread        {snapshot.spread_ticks} ticks "
                f"({relative_spread_bps(snapshot):.2f} bps)"
            )
            print(f"  microprice    {microprice_ticks(snapshot):.2f} ticks")
            print(f"  imbalance     {top_imbalance(snapshot):+.3f}")
            print(
                f"  cumulative vol {state.market_volume_base:,} "
                f"(rolling 60s: {state.recent_volume_base:,})"
            )
            print(
                f"  depth (5)     bid {snapshot.depth_base(Side.BUY, 5):,} "
                f"ask {snapshot.depth_base(Side.SELL, 5):,}"
            )
            print()

    # A snapshot is a value object: mutating the book afterwards does not change
    # a snapshot taken earlier, and a snapshot cannot be mutated at all.
    before = book.snapshot(3)
    print("Snapshots are immutable value objects:")
    print(f"  bids before            {[level.price_ticks for level in before.bids]}")
    try:
        before.bids = ()  # type: ignore[misc]
        print("  ERROR: the snapshot accepted a mutation")
        return 1
    except Exception as exc:
        print(f"  mutation refused: {type(exc).__name__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
