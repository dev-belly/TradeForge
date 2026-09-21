# Architecture

## 1. Layers

```
┌──────────────────────────────────────────────────────────────┐
│ interfaces     cli (typer) │ api (FastAPI) │ dashboard (Streamlit) │
├──────────────────────────────────────────────────────────────┤
│ infrastructure config │ storage(parquet/duckdb) │ logging │ cpp_bridge │
├──────────────────────────────────────────────────────────────┤
│ application    replay use-cases │ execution use-cases │ research use-cases │
├──────────────────────────────────────────────────────────────┤
│ domain         events │ orders │ book │ fills │ money │ capability │ policy protocols │
└──────────────────────────────────────────────────────────────┘
                    ▲
              C++20 core (pybind11 narrow façade)
              events decode │ book │ matching │ replay loop
```

Dependency arrows point **upward**. `domain` imports nothing above it.

## 2. Package map

| Package | Responsibility |
|---|---|
| `domain/` | Enums, value objects, protocols, capability checks. No frameworks. |
| `data/` | Adapters, validation, normalization, sample generation. |
| `orderbook/` | `MbpBook`, `MboBook`, levels, snapshots, derived book metrics. |
| `matching/` | Simulated matching engine (price-time priority). |
| `replay/` | Event-driven replay driver, clock, latency model. |
| `queue/` | `ExactQueueModel` (MBO) and `ApproximateQueueModel` (MBP). |
| `microstructure/` | Causal feature engine (spread/depth/imbalance/microprice/OFI/flows). |
| `execution/` | Parent/child orders, policies (TWAP/VWAP/POV/IS), OMS, guards, simulator. |
| `costs/` | Fee, spread, slippage, impact models. |
| `tca/` | Metrics, benchmarks, attribution, markouts. |
| `models/` | ML baselines, time-safe datasets, evaluation. |
| `experiments/` | Run registry, manifest, reproduce. |
| `reporting/` / `visualization/` | HTML report assembly / figure rendering. |
| `infrastructure/` | Config loading, storage, structured logging, C++ bridge. |
| `api/`, `cli/`, `dashboard/` | Interfaces. |

## 3. Key protocols (change points, and nothing else)

```python
class MarketDataAdapter(Protocol):  def events(self) -> Iterator[MarketEvent]
class ExecutionPolicy(Protocol):    on_market_event / on_timer / on_fill / on_reject
class QueueModel(Protocol):         on_event(...) -> QueueUpdate; queue_ahead(...)
class LatencyModel(Protocol):       arrival_ns(decision_ns) -> int
class CostModel(Protocol):          fees(fill) -> FeeBreakdown
class ImpactModel(Protocol):        impact_bps(...) -> float
class BookProtocol(Protocol):       apply(event); snapshot(depth)
```

## 4. Python ↔ C++

See ADR-002. Production core = C++. The Python book/matcher is a
differential-test oracle and a no-compiler fallback, always labelled
`engine_backend` in run manifests.

## 5. Data flow (execution experiment)

```
adapter.events() → validator → book.apply → MarketState
                                              ↓
                                    policy.on_market_event
                                              ↓ child order
                                    latency model → arrival time
                                              ↓
                              queue model / matching engine → fills
                                              ↓
                              cost model + impact → TCA attribution
                                              ↓
                                    run manifest + parquet artifacts
```

## 6. Enforced invariants

* No module under `domain/` imports a framework (architecture test).
* No cycles (architecture test over the import graph).
* Python files ≤ 600 lines, functions ≤ 100 lines (test-enforced gate).
* No `float` price comparison in book/matching (integer ticks only).
* No `time.sleep` for latency (grep-based test).
