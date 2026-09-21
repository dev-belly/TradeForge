"""Parquet writers.

Parquet is the interchange format and the archive; DuckDB is the query engine
that reads it. Nothing is written directly into a database file, so a result set
can always be inspected, diffed and re-ingested without the platform.

One directory per table, one file per run. That layout keeps a failed run
isolated and makes `glob` a valid discovery mechanism.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..domain.events import MarketEvent
from ..domain.fills import Fill
from ..domain.orders import ChildOrder
from ..execution.result import ExecutionResult
from ..tca.report import TcaReport

try:  # pyarrow is an optional extra; the core must import without it
    import pyarrow as pa
    import pyarrow.parquet as pq

    _PYARROW_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only in minimal installs
    pa = None  # type: ignore[assignment]
    pq = None  # type: ignore[assignment]
    _PYARROW_AVAILABLE = False


@dataclass(frozen=True, slots=True)
class WriteResult:
    table: str
    path: Path
    n_rows: int


class ParquetWriter:
    """Writes one Parquet file per (table, run) pair."""

    def __init__(self, root: Path | str) -> None:
        if not _PYARROW_AVAILABLE:
            raise RuntimeError("pyarrow is required for Parquet output; install tradeforge[full]")
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def _write(self, table: str, rows: Sequence[Mapping[str, Any]], filename: str) -> WriteResult:
        directory = self._root / table
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / filename
        if not rows:
            # An empty table is written as a schema-only file rather than
            # skipped: "no rows" and "the run never happened" are different
            # facts and a downstream query must be able to tell them apart.
            pq.write_table(pa.table({}), path)
            return WriteResult(table=table, path=path, n_rows=0)
        table_obj = pa.Table.from_pylist([dict(r) for r in rows])
        pq.write_table(table_obj, path, compression="snappy")
        return WriteResult(table=table, path=path, n_rows=len(rows))

    # ----------------------------------------------------------------- tables

    def write_events(
        self, events: Iterable[MarketEvent], *, dataset_name: str, seed: int
    ) -> WriteResult:
        rows = [
            {
                "dataset_name": dataset_name,
                "seed": seed,
                "sequence_id": e.sequence_id,
                "exchange_timestamp_ns": e.exchange_timestamp_ns,
                "receive_timestamp_ns": e.receive_timestamp_ns,
                "symbol": e.symbol,
                "event_type": e.event_type.value,
                "side": e.side.value if e.side else None,
                "price_ticks": e.price_ticks,
                "quantity_base": e.quantity_base,
                "order_id": e.order_id,
                "trade_id": e.trade_id,
                "flags": int(e.flags),
                "source": e.source,
            }
            for e in events
        ]
        return self._write("events", rows, f"{dataset_name}-seed{seed}.parquet")

    def write_execution(
        self,
        result: ExecutionResult,
        *,
        run_id: str,
        seed: int,
        config_fingerprint: str,
    ) -> WriteResult:
        row = {
            "run_id": run_id,
            "seed": seed,
            "config_fingerprint": config_fingerprint,
            **result.to_dict(),
        }
        return self._write("executions", [row], f"{run_id}-seed{seed}.parquet")

    def write_child_orders(
        self, orders: Sequence[ChildOrder], *, run_id: str, seed: int
    ) -> WriteResult:
        rows = [
            {
                "run_id": run_id,
                "seed": seed,
                "client_order_id": o.client_order_id,
                "parent_order_id": o.parent_order_id,
                "slice_index": o.slice_index,
                "side": o.side.value,
                "quantity_base": o.quantity_base,
                "filled_base": o.filled_base,
                "status": o.status.value,
                "style": o.style.value,
                "order_type": o.order_type.value,
                "time_in_force": o.time_in_force.value,
                "placed_price_ticks": o.placed_price_ticks,
                "estimated_queue_ahead_base": o.estimated_queue_ahead_base,
                "created_ns": o.created_ns,
                "working_ns": o.working_ns,
                "terminal_ns": o.terminal_ns,
            }
            for o in orders
        ]
        return self._write("child_orders", rows, f"{run_id}-seed{seed}.parquet")

    def write_fills(self, fills: Sequence[Fill], *, run_id: str, seed: int) -> WriteResult:
        rows = [
            {
                "run_id": run_id,
                "seed": seed,
                "fill_id": f.fill_id,
                "client_order_id": f.client_order_id,
                "parent_order_id": f.parent_order_id,
                "symbol": f.symbol,
                "side": f.side.value,
                "price_ticks": f.price_ticks,
                "quantity_base": f.quantity_base,
                "timestamp_ns": f.timestamp_ns,
                "liquidity_flag": f.liquidity_flag.value,
                "fee": float(f.fee),
                "queue_wait_ns": f.queue_wait_ns,
                "sequence_id": f.sequence_id,
            }
            for f in fills
        ]
        return self._write("fills", rows, f"{run_id}-seed{seed}.parquet")

    def write_tca(
        self, report: TcaReport, *, run_id: str, seed: int
    ) -> tuple[WriteResult, WriteResult]:
        metrics = report.metrics.to_dict()
        attribution = report.attribution.to_dict()
        tca_row = {
            "run_id": run_id,
            "seed": seed,
            "policy": metrics["policy"],
            "side": metrics["side"],
            "fill_ratio": metrics["fill_ratio"],
            "arrival_mid_ticks": metrics["arrival_mid_ticks"],
            "interval_vwap_ticks": metrics["interval_vwap_ticks"],
            "interval_twap_ticks": metrics["interval_twap_ticks"],
            "interval_mid_ticks": metrics["interval_mid_ticks"],
            "terminal_mid_ticks": metrics["terminal_mid_ticks"],
            "cost_vs_arrival_bps": metrics["cost_vs_arrival_bps"],
            "cost_vs_vwap_bps": metrics["cost_vs_vwap_bps"],
            "cost_vs_twap_bps": metrics["cost_vs_twap_bps"],
            "cost_vs_interval_mid_bps": metrics["cost_vs_interval_mid_bps"],
            "cost_vs_terminal_bps": metrics["cost_vs_terminal_bps"],
            "implementation_shortfall_bps": metrics["implementation_shortfall_bps"],
            "spread_cost_bps": attribution["spread_cost_bps"],
            "fees_bps": attribution["fees_bps"],
            "timing_bps": attribution["timing_bps"],
            "residual_impact_bps": attribution["residual_impact_bps"],
            "opportunity_cost_bps": attribution["opportunity_cost_bps"],
            "n_mid_observations": metrics["n_mid_observations"],
            "n_trade_prints": metrics["n_trade_prints"],
            "window_volume_base": metrics["window_volume_base"],
        }
        markout_rows = [{"run_id": run_id, "seed": seed, **m.to_dict()} for m in report.markouts]
        return (
            self._write("tca_metrics", [tca_row], f"{run_id}-seed{seed}.parquet"),
            self._write("markouts", markout_rows, f"{run_id}-seed{seed}.parquet"),
        )

    def write_dataset(
        self,
        *,
        dataset_name: str,
        dataset_version: str,
        data_type: str,
        provenance: str,
        redistributable: bool,
        license_name: str | None,
        config_fingerprint: str,
    ) -> WriteResult:
        row = {
            "dataset_name": dataset_name,
            "dataset_version": dataset_version,
            "data_type": data_type,
            "provenance": provenance,
            "redistributable": redistributable,
            "license": license_name,
            "config_fingerprint": config_fingerprint,
            "ingested_at_utc": datetime.now(tz=UTC).isoformat(),
        }
        return self._write(
            "datasets",
            [row],
            f"{dataset_name}-{dataset_version}-{config_fingerprint}.parquet",
        )

    def write_experiment_rows(self, rows: Sequence[Mapping[str, Any]]) -> WriteResult:
        return self._write("experiment_runs", rows, "experiment_runs.parquet")
