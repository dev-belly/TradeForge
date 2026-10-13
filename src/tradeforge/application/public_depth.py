"""Offline aggressive matching on complete public L2 depth update batches.

This deliberately scopes the experiment to displayed-book matching. It does not
route orders, model exact passive queues, infer sub-millisecond market states or
claim that hypothetical orders change the subsequent market.
"""

from __future__ import annotations

import bisect
import csv
import gzip
import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..domain.book import BookSnapshot, PriceLevel
from ..domain.enums import Side
from ..matching.engine import match_order


def load_depth_capture(path: Path) -> tuple[dict[str, Any], list[BookSnapshot]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("data_kind") != "public_market_capture":
        raise ValueError("Expected an explicitly identified public market capture")
    tick, step = Decimal(payload["tick_size"]), Decimal(payload["quantity_step"])
    if not tick.is_finite() or tick <= 0 or not step.is_finite() or step < 1 or step != int(step):
        raise ValueError(
            "This experiment requires an integral base-asset quantity step; no rounding"
        )

    def level(pair: list[str]) -> tuple[int, int]:
        price, quantity = Decimal(pair[0]) / tick, Decimal(pair[1])
        if not price.is_finite() or price <= 0 or price != int(price):
            raise ValueError("Capture price is not an exact positive integer tick")
        if not quantity.is_finite() or quantity < 0 or quantity != int(quantity) or quantity % step:
            raise ValueError("Capture quantity is not an exact integral base-asset amount")
        return int(price), int(quantity)

    initial = payload["snapshot"]
    bids = dict(level(pair) for pair in initial["bids"])
    asks = dict(level(pair) for pair in initial["asks"])
    if len(bids) < 10 or len(asks) < 10:
        raise ValueError("Capture requires at least ten initial levels on each side")
    lower_bid, upper_ask = min(bids), max(asks)
    update_id = int(initial["lastUpdateId"])
    snapshots: list[BookSnapshot] = []
    for packet in payload["depth_events"]:
        if packet["e"] != "depthUpdate" or packet["s"] != payload["symbol"]:
            raise ValueError("Unexpected depth message or symbol")
        first, last = int(packet["U"]), int(packet["u"])
        if first > last:
            raise ValueError("Invalid depth sequence interval")
        if last <= update_id:
            continue
        if first > update_id + 1:
            raise ValueError("Depth sequence gap: a new snapshot is required")
        # Apply all levels before constructing a state: a partial packet is not
        # a tradable book and can transiently appear crossed.
        for book, key in ((bids, "b"), (asks, "a")):
            for pair in packet[key]:
                price, quantity = level(pair)
                if quantity:
                    book[price] = quantity
                else:
                    book.pop(price, None)
        bid_prices = sorted(bids, reverse=True)[:10]
        ask_prices = sorted(asks)[:10]
        if (
            len(bid_prices) < 10
            or len(ask_prices) < 10
            or bid_prices[-1] < lower_bid
            or ask_prices[-1] > upper_ask
        ):
            raise ValueError("Top ten left the fully known initial snapshot range")
        current = BookSnapshot(
            timestamp_ns=int(packet["E"]) * 1_000_000,
            sequence_id=last,
            symbol=payload["symbol"],
            bids=tuple(PriceLevel(price, bids[price]) for price in bid_prices),
            asks=tuple(PriceLevel(price, asks[price]) for price in ask_prices),
        )
        if current.is_crossed or (snapshots and current.timestamp_ns < snapshots[-1].timestamp_ns):
            raise ValueError("Captured final books are crossed or timestamps regressed")
        snapshots.append(current)
        update_id = last
    if len(snapshots) < 2:
        raise ValueError("Capture has no continuous post-snapshot depth episode")
    return payload, snapshots


def _execution_rows(
    snapshots: list[BookSnapshot],
    times: list[int],
    start: int,
    duration: int,
    quantity_base: int,
    step: int,
    reference: float,
    tick: float,
    fee_scenarios_bps: tuple[float, ...],
    latency_scenarios_ns: tuple[int, ...],
) -> list[dict[str, Any]]:
    def known_at(instant: int) -> BookSnapshot:
        return snapshots[bisect.bisect_right(times, instant) - 1]

    units, remainder = divmod(quantity_base // step, 10)
    schedules = {
        "immediate": [(start, quantity_base)],
        "twap_10_slices": [
            (start + i * duration // 9, (units + int(i < remainder)) * step) for i in range(10)
        ],
    }
    rows = []
    for method, schedule in schedules.items():
        for latency in latency_scenarios_ns:
            filled, tick_notional, observed_book_age = 0, 0, 0
            for decision, requested in schedule:
                state = known_at(decision + latency)
                matched = match_order(state, side=Side.BUY, quantity_base=requested)
                filled += matched.filled_base
                tick_notional += sum(
                    part.price_ticks * part.quantity_base for part in matched.matched
                )
                observed_book_age = max(observed_book_age, decision + latency - state.timestamp_ns)
            notional = tick_notional * tick
            shortfall = (tick_notional / filled / reference - 1) * 10_000 if filled else None
            for fee in fee_scenarios_bps:
                fees = notional * fee / 10_000
                total = (
                    shortfall + fees / (reference * tick * filled) * 10_000
                    if shortfall is not None
                    else None
                )
                rows.append(
                    {
                        "method": method,
                        "latency_scenario_ns": latency,
                        "taker_fee_scenario_bps": fee,
                        "requested_base": quantity_base,
                        "filled_base": filled,
                        "fill_ratio": filled / quantity_base,
                        "executed_notional_quote": notional,
                        "fees_quote": fees,
                        "price_shortfall_bps": shortfall,
                        "executed_cost_bps": total,
                        "max_observed_book_age_ns": observed_book_age,
                    }
                )
    return rows


def run_public_depth(
    capture: Path,
    destination: Path,
    *,
    quantity_base: int = 1000,
    fee_scenarios_bps: tuple[float, ...] = (0.0, 5.0, 10.0),
    latency_scenarios_ns: tuple[int, ...] = (0, 1_000_000, 10_000_000),
) -> dict[str, Any]:
    payload, snapshots = load_depth_capture(capture)
    step = int(Decimal(payload["quantity_step"]))
    if type(quantity_base) is not int or quantity_base < 10 * step or quantity_base % step:
        raise ValueError("Quantity must support ten positive integral-step slices")
    if not fee_scenarios_bps or any(not math.isfinite(x) or x < 0 for x in fee_scenarios_bps):
        raise ValueError("Fee scenarios must be finite nonnegative basis points")
    if not latency_scenarios_ns or any(type(x) is not int or x < 0 for x in latency_scenarios_ns):
        raise ValueError("Latency scenarios must be nonnegative integer nanoseconds")
    times = [snapshot.timestamp_ns for snapshot in snapshots]
    start = times[0] + 3_000_000_000
    duration = 20_000_000_000
    if start + duration + max(latency_scenarios_ns) > times[-1]:
        raise ValueError("Capture does not cover the complete declared execution window")

    def known_at(instant: int) -> BookSnapshot:
        index = bisect.bisect_right(times, instant) - 1
        if index < 0:
            raise ValueError("No observed book available by the requested time")
        return snapshots[index]

    reference = known_at(start).mid_ticks
    assert reference is not None
    tick = float(Decimal(payload["tick_size"]))
    rows = _execution_rows(
        snapshots,
        times,
        start,
        duration,
        quantity_base,
        step,
        reference,
        tick,
        fee_scenarios_bps,
        latency_scenarios_ns,
    )
    result = {
        "data_kind": "public_market_capture",
        "symbol": payload["symbol"],
        "quote_currency": payload["quote_currency"],
        "source": payload["source"],
        "capture_sha256": hashlib.sha256(capture.read_bytes()).hexdigest(),
        "tick_size": payload["tick_size"],
        "quantity_step": payload["quantity_step"],
        "continuous_depth_batches": len(snapshots),
        "first_event_ns": times[0],
        "last_event_ns": times[-1],
        "start_ns": start,
        "end_ns": start + duration,
        "reference_mid_ticks": reference,
        "protocol": (
            "BUY; same quantity/window; complete packet states; "
            "as-of published E timestamps; fixed scenarios"
        ),
        "limitations": [
            "One short live episode; not representative venue performance or calibrated execution.",
            "100ms aggregated L2 feed; intra-batch changes and order identities are unobserved.",
            "Fees and order latencies are scenarios, not account fees or measured latency.",
            "Hypothetical fills do not alter subsequent market states or model endogenous impact.",
            "Costs cover executed quantity only; also consider fill ratio and unfilled quantity.",
            "This validates aggressive matching, not the full OMS or passive queue simulator.",
        ],
        "rows": rows,
    }
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "results.json").write_text(
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    with (destination / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return result
