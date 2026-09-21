# TradeForge Design Review

> Status: **Design approved — implementation in progress.**
> This document is the single source of truth for scope, market assumptions and
> architectural decisions. Any change that contradicts this file requires an ADR.
>
> Last reviewed: 2026-09-08

---

## 1. Problem Definition

Most open-source "quant trading" repositories model markets as **OHLCV bars** and
fill orders at `close` (or worse, at `mid`). That model cannot answer any question
a real execution desk cares about:

* What did it cost me to cross the spread?
* Did my passive order actually get filled, or did it sit behind 40k shares of queue?
* How much of my slippage was timing, how much was impact, how much was fees?
* Would a slower schedule have been cheaper? At what price risk?

TradeForge exists to answer those questions **honestly** on **event-level data**.

**One-sentence problem statement**

> Given a stream of historical market events with a known, declared data
> capability, reconstruct the observable limit order book, simulate the placement
> and lifecycle of our own child orders under explicit latency / queue / cost
> assumptions, and attribute the resulting implementation shortfall into
> interpretable economic components — with every approximation labelled.

---

## 2. Target Users

| User | What they get |
|---|---|
| Quant student | A runnable LOB: add/cancel/modify/trade → book → spread → depth. |
| Quant interview candidate | `docs/interview-notes.md` + 120 Q&A tied to real code paths. |
| Quant developer | Clean Python/C++ boundary, integer-tick domain, event-driven replay. |
| Microstructure researcher | Feature engine with leakage-safe rolling windows, markouts, OFI. |
| Execution quant | TWAP / VWAP / POV / IS baselines + full TCA attribution + sensitivity grids. |
| C++ engineer | A real (not decorative) C++20 LOB + matching core with benchmarks. |

**Explicitly not targeted:** anyone looking for a profitable trading bot, a
production exchange gateway, or a colocated HFT stack.

---

## 3. Research Questions

Each question below is answerable with the data capability we declare.

1. **RQ1 — Spread cost.** What fraction of implementation shortfall is pure
   quoted-spread crossing, by participation rate?
2. **RQ2 — Queue realism.** Under L2 (MBP) data, how much does the fill-rate
   estimate move between optimistic / neutral / conservative cancel-ahead
   assumptions? *(Hypothesis: it dominates. This is a headline result, not a bug.)*
3. **RQ3 — Latency sensitivity.** At the data's native timestamp resolution, how
   does cost change across a latency scenario grid?
4. **RQ4 — Algorithm comparison.** Do TWAP / VWAP / POV differ in cost, and is
   the ranking stable under a block bootstrap?
5. **RQ5 — Passive vs aggressive.** When does passive placement beat crossing
   after accounting for non-completion (opportunity cost)?
6. **RQ6 — Adverse selection.** What is the post-fill markout profile
   (e.g. 100 ms / 1 s / 5 s) for passive fills vs aggressive fills?
7. **RQ7 — ML economic value.** Does a fill-probability model improve realised
   execution cost versus a simple imbalance-threshold baseline?

---

## 4. Scope

**In scope**

* Normalized event schema + adapters (canonical parquet/csv, LOBSTER MBO,
  Binance public L2, deterministic synthetic sample).
* Event validation (STRICT / WARN / REPAIR) with a machine-readable report.
* MBP (market-by-price) book reconstruction; MBO (market-by-order) book with
  exact FIFO queue when the data source provides order identity.
* A matching engine for *simulated* crossing of our own orders (price-time
  priority, partial fills, IOC, cancel, replace).
* Event-driven historical replay with an explicit latency model that advances
  market time.
* Queue model with declared EXACT / APPROXIMATE mode and cancel-ahead policy.
* Microstructure features: spread, depth, imbalance, microprice, OFI, flows,
  realized volatility, trade/quote/cancel intensity.
* Execution: TWAP, historical-volume-profile VWAP, POV, IS baseline, passive
  and aggressive child-placement policies, research-grade OMS state machine.
* Cost model: commission, maker/taker fees, spread, slippage, simplified impact.
* TCA: arrival, VWAP, TWAP, markouts, fill rate, completion, participation,
  attribution, opportunity cost.
* Experiment registry, reproducible runs, HTML reports, CLI, FastAPI, Streamlit
  dashboard reading only real artifacts.
* C++20 core for event decoding, book, matching and replay loop + pybind11.

**Non-goals**

* Not a production exchange matching engine. No certification, no FIX gateway.
* Not a live trading system. No broker/exchange connectivity for order entry.
* Not an alpha / price-prediction product. Direction research exists only as a
  *baseline* for execution-cost questions.
* Not a colocated low-latency stack. No DPDK, kernel bypass, FPGA, huge pages.
* Not a portfolio optimizer or risk system.

---

## 5. Market Assumptions (declared, not hidden)

| # | Assumption | Consequence if violated |
|---|---|---|
| A1 | Exchange matches by **price then time** priority. | FIFO queue order wrong (e.g. pro-rata venues). |
| A2 | Our simulated child orders are **small relative to market volume**; replay approximation ignores our own market impact on the *future* event stream. | Impact understated at high participation. |
| A3 | Events are processed in ascending `(exchange_timestamp, sequence_id)`. | Fill sequencing wrong. |
| A4 | Tick size, lot size, fees are **instrument/venue configuration**, never literals. | Cost results wrong. |
| A5 | If the source is L2/MBP, **exact FIFO queue position is not observable**. | Any "exact" claim is invalid. |
| A6 | Latency values are **scenarios**, not measurements (no hardware measurement in this repo). | "Measured latency" claims invalid. |
| A7 | Trades in the event stream are assumed to have executed against the *resting* side at the time of the trade event. | Aggressor-side inference is dataset dependent. |

---

## 6. Event Semantics

### 6.1 Normalized event schema

Every source is normalized into one frozen record before it touches the domain:

| Field | Type | Notes |
|---|---|---|
| `sequence_id` | int64 | Source-local monotonic id. Gap != error (auctions, halts). |
| `exchange_timestamp_ns` | int64 | Event time at the venue. **Never synthesized.** |
| `symbol` | str | Normalized instrument symbol. |
| `event_type` | `EventType` enum | ADD / CANCEL / MODIFY / REPLACE / TRADE / CLEAR / SNAPSHOT / HALT / RESUME |
| `side` | `Side \| None` | Required for book-affecting events. |
| `price_ticks` | int64 \| None | Integer ticks. No float in the core. |
| `quantity` | int64 \| None | Base-asset units (see §9). |
| `order_id` | int64 \| None | Present only for MBO/L3 sources. |
| `trade_id` | int64 \| None | Present for TRADE. |
| `flags` | int | Bitfield (aggressor side, auction, intermarket-sweep …). |
| `source` | str | Adapter provenance. |

**Ordering key** is `(exchange_timestamp_ns, sequence_id)`. Same-timestamp events
keep source sequence order — they are *not* re-sorted, because re-ordering
invented information we do not have.

### 6.2 Timestamps

Three distinct concepts, never conflated:

* `exchange_timestamp_ns` — when the venue says the event happened.
* `receive_timestamp_ns` — when our feed handler saw it. **Only populated if the
  source provides it.** We never fabricate it.
* `simulation_timestamp_ns` — simulation clock value when we processed it.

If a source provides only an exchange timestamp, the simulation clock is driven
by `exchange_timestamp_ns` and `receive_timestamp_ns` stays `None`. Latency is
then applied as a *scenario offset*, clearly labelled.

---

## 7. Data Capability: L1 / L2 / L3, MBP / MBO

| Tier | Content | Enables | Cannot enable |
|---|---|---|---|
| L1 | Best bid/ask + sizes | quoted spread, mid | depth, imbalance beyond top |
| L2 / MBP | Aggregated size per price level | depth curve, multi-level imbalance, approximate queue | order identity, exact queue, order-level cancel |
| L3 / MBO | Individual orders with ids | exact FIFO queue, order-level cancel attribution | — |

**Hard rule:** a feature or model that requires a capability the active source
does not provide must **refuse to run** (raise `DataCapabilityError`), not
silently degrade to a guess. The capability matrix in `docs/data-capabilities.md`
is enforced by `tradeforge.domain.capability.require()`.

**MBP ≠ MBO.** An MBP level update of `-10` can mean one cancel of 10, ten
cancels of 1, or nine cancels plus a partial fill — indistinguishable. All
queue logic built on MBP is therefore an **estimate** and names say so
(`estimated_queue_ahead`, `ApproximateQueueModel`).

---

## 8. Order Book Architecture

Two books, one interface (`BookProtocol`), chosen by data capability:

* **`MbpBook` (market-by-price)** — `dict`-free; a `SortedPriceLevels` structure
  with O(1) best-price access and O(log n) level insert/erase. Aggregated
  quantities. Used for all L2 sources.
* **`MboBook` (market-by-order)** — price levels hold a FIFO `deque` of orders
  with identity. Exact queue position is the count of shares ahead in the deque.
  Used only for LOBSTER-style sources.

Both are **reconstruction** structures: they apply observed events. They are not
the venue. Neither one may be mutated by a strategy.

Metrics (spread, depth, imbalance, microprice) are *derived*, computed on a
`BookSnapshot` — they never live inside the book object.

---

## 9. Quantity & Price

* **Price** — `int64` ticks internally. Conversion to float only at the
  reporting/aggregation boundary via `InstrumentSpec.tick_size`.
  Matching core never compares floats for equality.
* **Quantity** — `int64` in **base-asset units** (shares / contracts / base CCY).
  Field names always carry the unit: `quantity_base`, `qty_base`, `volume_base`.
  A bare `size` is a code-review rejection.
* **Notional** — computed as `price_ticks * tick_size * quantity_base`.

---

## 10. Matching Engine (simulation only)

The matching engine answers: *if our child order arrived at time T against the
reconstructed book, what would it trade?*

* Supports MARKET, LIMIT, IOC, (optional FOK).
* Price-time priority, multi-level sweep, partial fills with remaining quantity
  carried.
* Cancel and replace (replace = cancel + new; loses queue — explicitly modelled).
* Validated by hand-checkable fixtures (see §23).

**It is not the historical replay path.** Replay reconstructs what *did* happen
from data; matching simulates what *would* happen to our order. Two different
code paths, two different test suites. They are never merged.

---

## 11. Replay Architecture & the Counterfactual Problem

```
for event in event_source:                 # lazy, never materialized
    clock.advance_to(event.ts)
    drain_due_actions(clock)               # our orders whose latency has elapsed
    book.apply(event)                      # observable state moves
    state = observe(book, clock)           # ONLY past + present
    policy.on_market_event(state)          # may emit child orders
    drain_immediate_actions()
```

**Counterfactual market response problem.** If we simulate a 5% participation
order, a real venue's future event stream would have been different. We do not
model that feedback. Mitigations, all labelled in output:

1. Default participation caps (config) keep our footprint small.
2. Every run records `counterfactual_mode = "replay_approximation"`.
3. Capacity numbers are reported as **sensitivity estimates**, never as
   "capacity = $X".

---

## 12. Future Event Leakage Controls

| Leak | Control | Test |
|---|---|---|
| Strategy reads future events | Event source is a lazy iterator; the policy only receives a `MarketState` value object | `tests/unit/test_future_event_leakage.py` |
| Future quote / trade / cancel in features | Rolling windows are causal (`<= t`), implemented over a bounded deque updated on event arrival | `test_no_future_trade_in_window`, `test_no_future_quote_in_window` |
| Full-day volume curve used to schedule same-day VWAP | VWAP profile is built **only** from `historical_volume_profile` (prior sessions) or a causal real-time estimator | `test_vwap_profile_excludes_current_day` |
| Full-day scaler fitted before trading | `NormalizationPolicy` forbids `fit` on a window that extends past the decision time | `test_no_full_day_normalization` |
| Same-timestamp look-ahead | Events at identical timestamp are delivered in source order; a policy acting at `t` has not yet seen the later event with the same `t` | `test_same_timestamp_ordering` |

---

## 13. Queue Modeling

| Mode | Condition | Semantics |
|---|---|---|
| `EXACT` | MBO/L3 source with order ids | `queue_ahead = Σ shares of orders ahead in FIFO deque` |
| `APPROXIMATE` | MBP/L2 source | Estimate with explicit update rules and a cancel-ahead policy |

### Approximate queue update rules (MBP)

State per resting child order: `estimated_queue_ahead` (shares).

* On best-level display size **decrease** `Δ<0`: we cannot know whether removed
  shares were ahead or behind. Split by policy:
  * `OPTIMISTIC`: 100% of the decrease is attributed **behind** us → queue ahead unchanged.
  * `NEUTRAL`: split proportionally to our share of the level (`queue_ahead / display_size`).
  * `CONSERVATIVE`: 100% attributed **ahead** → queue ahead reduced first.
* On **trade** at our price: reduce queue ahead by traded quantity (trades consume
  from the front of the queue); if queue ahead < 0 then our order trades the remainder.
* On our **replace**: queue position is lost (reset to end of level).
* Never `if price_touched: fill()`.

All three policies are run in the queue-sensitivity experiment; the spread
between them **is a result**.

---

## 14. Fill Modeling

States: `MARKETABLE`, `RESTING`, `PARTIAL`, `FILLED`, `CANCELLED`, `EXPIRED`,
`REJECTED`.

Each fill records: `fill_price_ticks`, `fill_qty_base`, `fill_time_ns`,
`liquidity_flag` (MAKER/TAKER), `queue_wait_ns` (0 for aggressive fills).

Aggressive fills cross the book through the matching engine and pay the spread.
Passive fills only occur when the queue model says the queue ahead is consumed by
trades at our price — **price touch alone never fills a passive order.**

---

## 15. Execution Architecture

```
ParentOrder  →  ExecutionPolicy  →  ChildOrder[*]  →  OMS  →  Fill[*]  →  ExecutionResult
                     ▲                                  ▲
              (config-driven)                    LatencyModel, QueueModel, CostModel
```

* **ParentOrder**: symbol, side, quantity_base, start_ns, end_ns, urgency,
  participation_limit, optional limit price.
* **ExecutionPolicy**: implements `on_market_event(state)`, `on_timer(state)`,
  `on_fill(report)`, `on_reject(report)`. It receives **value objects only**;
  it cannot reach the simulator.
* **OMS**: explicit state machine with legal transitions (§17).
* **Guards**: max child size, max notional, max participation, max open orders,
  max inventory, kill switch. Simulation guardrails, not a risk system.

### Algorithms

| Algo | Scheduling basis | Leakage guard |
|---|---|---|
| TWAP | Uniform slices over `[start, end]` | none needed |
| VWAP | **Historical** volume profile from prior sessions (config `profile_source`) | refuses in-session full-day curve |
| POV | `target = rate × observed_market_volume_since_last_slice` | uses only observed (past) volume |
| IS baseline | Front-loaded schedule from urgency + risk aversion (Almgren–Chriss style, documented as simplified) | — |

All handle: slice rounding, remaining-quantity carry, missed slices, window-end
sweep, and non-completion → opportunity cost.

---

## 16. Cost, Impact, Latency

* **Cost model** (all from `configs/costs.yaml`): commission (bps or per-share),
  maker fee / rebate, taker fee, plus spread, slippage, impact computed per fill.
* **Spread**: aggressive fills pay `half_spread` (or full crossing vs arrival
  mid) — market orders never fill at mid.
* **Impact**: `impact_bps = k * sigma * sqrt(Q / ADV)` (square-root) and an
  optional linear variant. **Documented as a simplified research approximation.**
  Temporary vs permanent split is configurable (`permanent_fraction`), both
  defined in `docs/market-impact.md`.
* **Latency**: decision → network → exchange processing. Applied by advancing the
  simulation clock, **never** `time.sleep()`. Market events continue between
  decision and arrival. Values are *scenarios* from `configs/latency.yaml`.

**Sign convention (system-wide):** positive cost = worse execution;
negative cost = price improvement. Markout for a BUY = `future_mid − fill_price`;
for a SELL the sign is flipped.

---

## 17. Transaction Cost Analysis

```
Implementation Shortfall (bps)
  = side_sign * (avg_exec_price - arrival_price) / arrival_price * 1e4   [execution]
  + spread_cost_bps
  + fee_bps
  + impact_estimate_bps
  + timing_cost_bps
  + opportunity_cost_bps
```

Attribution is additive **by construction** with the residual reported
explicitly; components that are estimates are labelled `*_estimate`. Unexecuted
quantity never disappears — it is charged opportunity cost at the decision
horizon price.

Benchmarks: Arrival Price, Interval VWAP, Interval TWAP, Interval Mid,
Close (optional). Each benchmark's economic meaning is documented.

---

## 18. Python / C++ Boundary

**C++20 owns** (performance-sensitive, deterministic, integer-tick):
event decoding, MBP order book, matching core, replay loop driver, queue
simulation primitives.

**Python owns**: research, statistics, ML, experimentation, TCA, reporting, API,
CLI, dashboard, configuration.

**Binding**: pybind11, narrow interface only — a flat `ReplayEngine` façade plus
POD structs. The C++ object graph is **not** exposed. Python sees
`apply_event(...) -> BookSnapshotView`, `match(...) -> MatchResult`,
`run_segment(...) -> SegmentResult`.

**Single production core rule.** The C++ implementation is production. The
Python book/matcher exists **solely** as a differential-test oracle and a
fallback when the extension is not compiled. Every run records
`engine_backend` (`cpp` | `python-reference`) in its manifest. A differential
test asserts byte-identical snapshots on randomized event streams.

---

## 19. Dependency Direction

```
domain  ←  application  ←  infrastructure  ←  interfaces(api, cli, dashboard)
```

`domain` imports nothing above it and nothing framework-shaped. Enforced by
`tests/architecture/test_dependency_direction.py`, which walks the AST of every
module under `src/tradeforge/domain` and fails on any import of
fastapi/streamlit/sqlalchemy/pandas/mlflow/duckdb/click/typer.

Change points that justify an abstraction (and only these): data source,
execution policy, queue model, latency model, cost model, impact model.

---

## 20. Storage & SQL

* Raw normalized events → **Parquet** (partitioned by `symbol/date`).
* Book snapshots, fills, execution results, TCA rows → **Parquet**.
* Experiment metadata → **DuckDB** (`artifacts/experiments.duckdb`).
* Tick events are never loaded into a row store.

Seven SQL scripts under `sql/` (execution summary, slippage by symbol, latency
sensitivity, spread regime, fill analysis, daily TCA, experiment comparison),
each exercised by an integration test against a real DuckDB file.

---

## 21. Experiment Registry & Reproducibility

Every run writes a manifest: `run_id`, `git_commit`, `dataset_version`,
`symbol`, `date_range`, `market_data_type`, `book_mode`, `feature_version`,
`execution_algo`, `execution_params`, `queue_model`, `latency_model`,
`cost_model`, `impact_model`, `seed`, `engine_backend`, metrics, artifact paths.

`tradeforge experiment reproduce <run_id>` replays the stored config. Randomness
is seeded and only used for bootstrap / Monte-Carlo uncertainty / controlled
simulation — never for "random success".

---

## 22. Statistics & Anti-P-Hacking

* Block bootstrap (by day and by execution) — tick-level i.i.d. bootstrap is
  invalid under autocorrelation and is not offered.
* Paired comparisons for algorithm A vs B on the same parent-order set.
* Confidence intervals reported for every headline mean/median.
* Search space, trial count and selection rule are recorded in the manifest;
  threshold sweeps must pre-register their grid.

---

## 23. Testing Strategy

| Layer | Contents |
|---|---|
| Unit | event parsing/ordering/duplicates, book add/cancel/modify/depth/spread, matching priority/FIFO/partial/multi-level/IOC, queue depletion, TWAP/VWAP/POV/remaining/completion, TCA sign conventions |
| Hand-checkable fixtures | Bid 100×10, 99×20 / Ask 101×5, 102×20; Market Buy 12 ⇒ 5@101 + 7@102 — asserted explicitly |
| Property (Hypothesis) | `filled <= ordered`, no negative quantity/depth, `best_bid <= best_ask`, inventory conservation |
| Differential | Python oracle vs C++ core on randomized streams |
| Integration | sample events → book → features → execution → fills → TCA |
| E2E | clean run: load → validate → replay → execute → TCA → report |
| Architecture | dependency direction, no framework in domain, no cycles, file/function size gates |
| Leakage | the seven controls in §12 |
| Fuzz (C++) | malformed input, invalid sequence/quantity into the event decoder |

Coverage target ≥ 80 % overall, higher for `domain`, `orderbook`, `matching`.

---

## 24. Benchmarking

`make benchmark` produces: events/sec, book updates/sec, replay throughput,
matching throughput, snapshot throughput, Python↔C++ boundary overhead, RSS.

Every number in the README comes from `artifacts/benchmarks/*.json`. The
environment block (CPU, RAM, OS, compiler, flags, Python, dataset, event count,
depth, warmup, runs) is recorded next to the numbers. Local microbenchmarks are
**not** exchange latency and are labelled as such.

---

## 25. Failure Modes

| Failure | Detection | Behaviour |
|---|---|---|
| Crossed book after event | validator / book invariant | STRICT: raise; WARN: log; REPAIR: log + repair record |
| Negative level depth | book invariant | as above |
| Duplicate sequence id | validator | as above |
| Invalid cancel (unknown order id) | MBO book | as above |
| Out-of-order timestamp | validator | as above |
| Unexpected CLEAR | validator | as above |
| Locked market (bid == ask) | validator | reported as an observation, not auto-fixed |
| Capability violation | `require()` | raise `DataCapabilityError` |

---

## 26. Known Limitations (summary; full list in `docs/limitations.md`)

1. Historical replay is counterfactual; our own orders do not alter the future
   event stream.
2. L2/MBP cannot yield exact FIFO queue position — only estimates.
3. Impact model is a simplified research approximation.
4. Latency values are scenarios, not measurements. No colocation, no kernel
   bypass, no certified gateway.
5. Public/sample data is incomplete relative to a proprietary full-feed.
6. Historical results ≠ live results. No production risk system.

---

## 27. Tradeoffs

| Decision | Chosen | Rejected alternative | Why |
|---|---|---|---|
| Book storage | Sorted levels + dense tick array window | `std::map` only | Cache locality and O(1) best price; map retained for far levels |
| Python book exists at all | Yes, as oracle | C++ only | Testability + usability without a compiler; differential-tested |
| Impact model | Square-root + linear, configurable | Calibrated proprietary model | No calibration data; transparency beats false precision |
| Replay feedback | None | Full agent-based market | Would be unvalidatable on this data |
| Experiment store | DuckDB + Parquet | PostgreSQL + MLflow | Zero-config `make demo` |
| ML | Baselines first, LightGBM optional | Deep learning | Sample size and interpretability |

---

## 28. Open-Source User Experience

* **One-command demo**: `git clone && cd tradeforge && make demo` → sample data →
  validate → replay → execute → TCA → `reports/demo/report.html`. No API key, no
  database, no download needed.
* **Demo mode vs research mode** are distinct; demo output is labelled
  `SYNTHETIC SAMPLE — NOT MARKET DATA`.
* README first screen states what/why/how in 10 seconds; architecture diagram is
  one figure; badges limited to CI / Python / C++ / License / Release.
* Guides under `docs/guides/` teach concepts (L1/L2/L3, MBP vs MBO, why price
  touch ≠ fill, microprice, OFI, queue position, maker vs taker,
  TWAP/VWAP/POV, IS, impact).

---

## 29. GitHub Launch Strategy

* Repo: `dev-belly/TradeForge`, MIT, description and topics per §191 of the
  build brief (only genuinely relevant topics).
* Release plan v0.1 → v1.0 following real progress; release notes include
  limitations and known failures.
* Distribution: teach-first content (why price touch ≠ fill; why L2 cannot show
  queue) with TradeForge as the reference implementation.
* No star fraud, no fake benchmarks, no fake UI. Failed experiments are kept.

---

## 30. Definition of Done (per phase)

`DESIGN → IMPLEMENT → RUN → TEST → REVIEW → ARCHITECTURE AUDIT →
MARKET REALITY AUDIT → QUEUE REALITY AUDIT → LEAKAGE AUDIT → FIX → RETEST →
BENCHMARK → DOCUMENT → COMMIT → NEXT`

A phase is done only when its tests pass **and** the audits above are recorded.
