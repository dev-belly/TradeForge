"""Microbenchmarks for the hot paths, with the environment recorded.

A throughput number without the machine, the commit and the interpreter is not a
measurement, it is an anecdote. Every artefact written here carries all three,
and the disclaimer that this is a local microbenchmark and has nothing to do
with exchange or network latency.

Method: warm up, then take the median of several measured runs. The median
rather than the mean because a single scheduler hiccup should not define the
result, and the full sample is written so a reader can see the spread.

Run: `make benchmark`
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (str(SRC), str(ROOT / "build" / "python")):
    if path not in sys.path:
        sys.path.insert(0, path)

from tradeforge.data.registry import create_adapter
from tradeforge.domain.enums import DataType, Side
from tradeforge.infrastructure.config import load_configs
from tradeforge.infrastructure.cpp_bridge import core_status
from tradeforge.matching import match_order
from tradeforge.microstructure import MicrostructureEngine
from tradeforge.orderbook import BookSettings, create_book
from tradeforge.research.registry import EnvironmentRecord

DISCLAIMER = (
    "Local microbenchmark. Not comparable to exchange or network latency. "
    "Recorded to detect regressions in this repository, nothing more."
)


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    name: str
    unit: str
    n_operations: int
    median_seconds: float
    best_seconds: float
    worst_seconds: float
    runs: tuple[float, ...]
    notes: str = ""

    @property
    def per_second(self) -> float:
        return self.n_operations / self.median_seconds if self.median_seconds > 0 else 0.0

    @property
    def nanos_per_operation(self) -> float:
        if self.n_operations == 0:
            return 0.0
        return self.median_seconds / self.n_operations * 1e9

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "unit": self.unit,
            "n_operations": self.n_operations,
            "median_seconds": self.median_seconds,
            "best_seconds": self.best_seconds,
            "worst_seconds": self.worst_seconds,
            "per_second": self.per_second,
            "nanos_per_operation": self.nanos_per_operation,
            "spread_ratio": (
                self.worst_seconds / self.best_seconds if self.best_seconds > 0 else None
            ),
            "notes": self.notes,
        }


def measure(
    name: str,
    operation: Callable[[], int],
    *,
    warmup: int = 2,
    runs: int = 7,
    unit: str = "ops",
    notes: str = "",
) -> BenchmarkResult:
    """Time `operation` repeatedly; it returns the number of operations it did."""
    n_operations = 0
    for _ in range(warmup):
        n_operations = operation()

    samples: list[float] = []
    for _ in range(runs):
        started = time.perf_counter()
        n_operations = operation()
        samples.append(time.perf_counter() - started)

    return BenchmarkResult(
        name=name,
        unit=unit,
        n_operations=n_operations,
        median_seconds=statistics.median(samples),
        best_seconds=min(samples),
        worst_seconds=max(samples),
        runs=tuple(samples),
        notes=notes,
    )


# ------------------------------------------------------------------ benchmarks


def bench_event_decode(events: list) -> BenchmarkResult:
    def run() -> int:
        total = 0
        for event in events:
            total += event.sequence_id & 1
            _ = event.event_type.value
        return len(events)

    return measure(
        "event_decode",
        run,
        unit="events",
        notes="normalized-event field access; the adapter's own decode is measured separately",
    )


def bench_book_update(events: list, data_type: DataType) -> BenchmarkResult:
    book = create_book(symbol="SYNTH", settings=BookSettings.from_dict({}), data_type=data_type)

    def run() -> int:
        nonlocal book
        book = create_book(symbol="SYNTH", settings=BookSettings.from_dict({}), data_type=data_type)
        applied = 0
        for event in events:
            book.apply(event)
            applied += 1
        return applied

    return measure(
        "book_update", run, unit="events", notes=f"book reconstruction ({data_type.value})"
    )


def bench_snapshot(events: list, data_type: DataType) -> BenchmarkResult:
    book = create_book(symbol="SYNTH", settings=BookSettings.from_dict({}), data_type=data_type)
    for event in events[:2_000]:
        book.apply(event)

    def run() -> int:
        for _ in range(200):
            book.snapshot(10)
        return 200

    return measure("snapshot", run, unit="snapshots", notes="top-10 snapshot construction")


def bench_matching(events: list, data_type: DataType) -> BenchmarkResult:
    book = create_book(symbol="SYNTH", settings=BookSettings.from_dict({}), data_type=data_type)
    for event in events[:2_000]:
        book.apply(event)
    snapshot = book.snapshot(50)

    def run() -> int:
        for _ in range(200):
            match_order(snapshot, side=Side.BUY, quantity_base=500)
        return 200

    return measure("matching", run, unit="matches", notes="crossing up to 500 base units")


def bench_features(events: list) -> BenchmarkResult:
    def run() -> int:
        local = MicrostructureEngine()
        book = create_book(
            symbol="SYNTH", settings=BookSettings.from_dict({}), data_type=DataType.L2_MBP
        )
        count = 0
        for event in events:
            book.apply(event)
            snapshot = book.snapshot(10)
            local.on_event(event, snapshot)
            local.to_market_state(snapshot)
            count += 1
        return count

    return measure(
        "feature_update",
        run,
        unit="events",
        notes="causal feature update plus MarketState construction",
    )


def bench_replay_throughput(events: list, data_type: DataType) -> BenchmarkResult:
    def run() -> int:
        book = create_book(symbol="SYNTH", settings=BookSettings.from_dict({}), data_type=data_type)
        engine = MicrostructureEngine()
        count = 0
        for event in events:
            book.apply(event)
            snapshot = book.snapshot(10)
            engine.on_event(event, snapshot)
            engine.to_market_state(snapshot)
            count += 1
        return count

    return measure(
        "replay_throughput",
        run,
        unit="events",
        notes="full replay loop: apply, snapshot, features, state",
    )


def bench_cpp_boundary(events: list) -> BenchmarkResult | None:
    """Measure the pybind11 crossing cost, if the extension is built."""
    try:
        import tradeforge_core
    except ImportError:
        return None

    payloads = [
        {
            "sequence_id": event.sequence_id,
            "exchange_timestamp_ns": event.exchange_timestamp_ns,
            "symbol": event.symbol,
            "event_type": event.event_type.value,
            "side": event.side.value if event.side else None,
            "price_ticks": event.price_ticks,
            "quantity_base": event.quantity_base,
            "flags": int(event.flags),
        }
        for event in events
    ]

    def run() -> int:
        book = tradeforge_core.MbpBook("SYNTH")
        count = 0
        for payload in payloads:
            book.apply(payload)
            count += 1
        return count

    return measure(
        "cpp_book_throughput",
        run,
        unit="events",
        notes=(
            "C++ book via pybind11. Includes the dict-to-struct conversion, which "
            "is the honest cost of the boundary: a Python caller pays it per event."
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="artifacts/benchmarks")
    parser.add_argument("--events", type=int, default=20_000)
    parser.add_argument("--seed", type=int, default=20260908)
    args = parser.parse_args()

    configs = load_configs(ROOT / "configs")
    options = dict(configs["market_data"]["source"]["options"])
    options["n_events"] = args.events
    options["seed"] = args.seed
    events = list(create_adapter("synthetic", options).events())

    data_type = DataType(configs["market_data"]["dataset"]["data_type"])
    results: list[BenchmarkResult] = [
        bench_event_decode(events),
        bench_book_update(events, data_type),
        bench_snapshot(events, data_type),
        bench_matching(events, data_type),
        bench_features(events),
        bench_replay_throughput(events, data_type),
    ]
    cpp = bench_cpp_boundary(events)
    if cpp is not None:
        results.append(cpp)

    environment = EnvironmentRecord.capture(ROOT)
    core = core_status()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "disclaimer": DISCLAIMER,
        "engine_backend": core["backend"],
        "n_events": len(events),
        "environment": environment.to_dict(),
        "benchmarks": [r.to_dict() for r in results],
    }
    path = output_dir / "python_benchmarks.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    width = max(len(r.name) for r in results) + 2
    print(f"SYNTHETIC data, {len(events):,} events, backend={core['backend']}")
    print(f"commit {environment.git_commit} (dirty={environment.git_dirty})")
    print()
    print(f"{'benchmark':<{width}}{'median':>12}{'ns/op':>12}{'ops/s':>14}")
    print("-" * (width + 38))
    for result in results:
        print(
            f"{result.name:<{width}}{result.median_seconds * 1e3:>10.2f}ms"
            f"{result.nanos_per_operation:>12.0f}{result.per_second:>14,.0f}"
        )
    print()
    print(DISCLAIMER)
    print(f"\nwritten: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
