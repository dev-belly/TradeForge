# TradeForge

[![CI](https://github.com/dev-belly/TradeForge/actions/workflows/ci.yml/badge.svg)](https://github.com/dev-belly/TradeForge/actions/workflows/ci.yml)

An event-driven market-microstructure and algorithmic-execution research platform:
a C++20 limit-order-book core, a Python research layer, and a transaction-cost
analysis stack that refuses to report a number it cannot justify.

CI runs on Python 3.11, 3.12 and 3.13, builds the C++ core and runs the
Python/C++ differential suite, repeats the C++ tests under AddressSanitizer and
UndefinedBehaviorSanitizer, and checks that a seed reproduces an execution and
that the git commit is determinable.

The design goal is not "a backtest that produces good numbers". It is **a
platform whose numbers can be trusted, including when they are disappointing**.
Most of the engineering effort here went into the things that make a result
believable: capability gating, causality tests, purged splits, leakage canaries,
paired statistics, and a provenance block on every artefact.

---

## Sixty-second tour

```bash
python -m pip install -e ".[dev]"
make demo
```

No downloads, no API keys, no network. `make demo` generates a deterministic
synthetic session, reconstructs the book from the event stream, runs four
execution algorithms in two placement styles, and prints the cost table.

```
SYNTHETIC DATA - deterministic generator, not a real venue.

policy       style       fill     maker   vs_arrival_bps  vs_vwap_bps  is_bps   participation
-----------  ----------  -------  ------  --------------  -----------  -------  -------------
twap         passive     100.00%  96.3%   -7.9369         2.8907       -7.9369  1.888%
twap         aggressive  100.00%  5.1%    -6.9336         3.8951       -6.9336  1.888%
vwap         passive     100.00%  96.3%   -7.9001         2.9276       -7.9001  1.888%
pov          passive     100.00%  100.0%  -5.0705         5.7602       -5.0705  1.888%
pov          aggressive  100.00%  3.4%    -3.3543         7.4782       -3.3543  1.888%
is_baseline  passive     100.00%  97.4%   -7.1082         3.7202       -7.1082  1.888%
```

**Read the first two cost columns together.** Every strategy looks excellent
against the arrival price and mediocre against the interval VWAP. That is not a
bug in the strategies; it is the arrival benchmark crediting them with 5.2 bps of
market drift that happened while the order was working. This distinction is the
single most important idea in the repository, and it is why the platform ships
with `sql/02_benchmark_disagreement.sql` and refuses to present one cost column
without the other.

---

## What makes the numbers defensible

Each of these is enforced by code and by a test, not by a paragraph in a doc.

| Claim | How it is enforced | Test |
|---|---|---|
| A capability the data cannot support is never fabricated | `domain/capability.py` raises instead of degrading | `tests/unit/test_domain.py::TestCapabilityGating` |
| A passive order never fills on price touch | fills only come from queue consumption | `tests/unit/test_queue.py::test_price_touch_alone_never_fills` |
| A policy cannot see the future | `MarketState` has no handle to the event stream; prefix-invariance tests | `tests/leakage/test_no_future_leakage.py` |
| VWAP is scheduled from **prior** sessions only | `VwapPolicy` raises at construction without a historical profile | `tests/leakage/…::TestVwapProfileLeakage` |
| Latency is market time, not wall time | `time.sleep` is banned in the simulation path by an architecture test | `tests/architecture/…::test_no_time_sleep_in_simulation_path` |
| The domain does not depend on its callers | AST-walked import rules, no cycles | `tests/architecture/test_layers.py` |
| ML splits are purged and never shuffled | `assert_disjoint`, `assert_splits_usable`, purge derived from the label horizon | `tests/unit/test_ml.py::TestPurgedSplit` |
| A leaky feature is caught | univariate feature screen at 0.90 AUC | `tests/unit/test_ml.py::TestFeatureLeakScreen` |
| Python and C++ do not drift | event-by-event parity on snapshots, levels, matching and errors | `tests/differential/test_python_cpp_parity.py` |
| Results are reproducible | same seed produces a byte-identical execution | `tests/integration/…::TestDeterminism` |

---

## The honest findings

The platform is more useful when it reports what did *not* work. Three results
from the bundled synthetic data, all reproducible with `make research` and
`make ml`:

**1. The benchmark choice changes the sign of the conclusion.** Arrival-price
cost is −7.94 bps; interval-VWAP cost is +2.89 bps. Same execution. Any claim of
the form "we beat the arrival price by X bps" is mostly a claim about which way
the market moved.

**2. With three sessions, no algorithm is distinguishable from any other.** The
paired comparison of VWAP against TWAP gives a mean difference of +0.013 bps
with a 95% interval of [−0.001, +0.037] and a sign-test p-value of 1.000. The
platform reports this rather than quoting the point estimate, and applies
Holm-Bonferroni across the family of comparisons.

**3. No machine-learning baseline's edge over the base rate is distinguishable
from noise.** The fill-within-30s target has a base rate of 0.85 on the test
split. Ridge regression scores 0.8585 against it — a lift of +0.94 percentage
points, with a 95% interval of [−0.91, +2.79]. The interval spans zero, so the
point estimate is not a finding. Two baselines are *significantly worse* than
the base rate: the mid-price rule and the imbalance threshold both have
intervals excluding zero on the wrong side.

The report shows the interval next to every lift, not just the point estimate,
and runs two independent leakage checks before anyone is tempted to quote an
AUC. An earlier version of this paragraph said "logistic regression and ridge
score at or below the base rate", which was simply false — ridge scores above
it. That is what the interval was added to catch.

```bash
make research   # pre-registered grids A-E, every cell recorded, losses included
make ml         # fill-probability baselines with both leakage checks
```

---

## Architecture

```
interfaces    CLI · HTTP API · dashboard · HTML reports
     │
application   harness: assemble the stack from config, run one replay
     │
 ┌───┴──────────────────────────────────────────────────────────┐
 │                                                              │
research   tca   ml        execution        replay        storage
 │         │     │              │              │              │
 └─────────┴─────┴──────┬───────┴──────────────┴──────────────┘
                       │
        microstructure · matching · queue · costs
                       │
                orderbook · data · domain
```

Dependencies point inward and only inward. `tests/architecture/test_layers.py`
walks the AST of every module and fails the build on a violation, including a
cycle. The core (`domain`, `orderbook`, `matching`, `queue`, `costs`) imports
nothing outside the standard library.

The C++ core is the production path; the Python implementation is the **reference
oracle** — the specification the C++ must agree with. Both exist because
differential testing needs two independent implementations, and because a
reviewer without a C++ toolchain must still be able to reproduce every number.

---

## Sign conventions

One convention, applied everywhere, so no table needs a footnote:

* **Cost in bps: positive is worse.** Enforced through `Side.sign`, so a buy
  below the benchmark and a sell above it both report negative.
* **Markout in bps: positive is adverse.** The mid moved against us after the
  fill.
* **Queue wait: non-negative.** A maker fill cannot precede the order joining
  the queue, and a test asserts it.

---

## Layout

```
src/tradeforge/
  domain/          value objects, enums, capability gating, protocols
  data/            adapters (synthetic · LOBSTER · Binance · normalized), validation
  orderbook/       MBP and MBO reconstruction, metrics, invariants
  matching/        simulated crossing of the observable book
  queue/           approximate (L2) and exact (L3) queue position models
  microstructure/  causal feature engine: OFI, volatility, depth, intensity
  execution/       policies, OMS, simulator, guardrails, volume profiles
  replay/          simulation clock, latency scenarios, replay driver
  costs/           fee, spread and impact models
  tca/             benchmarks, cost metrics, attribution, markouts, reports
  ml/              fill-probability baselines, purged split, leakage canaries
  research/        experiment grids, block bootstrap, paired comparison, registry
  storage/         Parquet writers, DuckDB query layer
  application/     the harness that wires it all together
  interfaces/      CLI, FastAPI, Streamlit, HTML report builder
cpp/               C++20 book and matching engine, pybind11 facade
sql/               seven analytical queries, each stating its question
configs/           every parameter; no market constant lives in code
docs/              ADRs, design review, guides, engineering notes
tests/             unit · integration · property · leakage · architecture · differential
```

---

## Commands

| Command | What it does |
|---|---|
| `make demo` | Four algorithms, two styles, one table |
| `make test` | The Python suite (322 test functions; the architecture suite alone expands to 506 parametrised cases) |
| `make test-differential` | Builds the C++ core and proves it matches Python |
| `make research` | Grids A–E with bootstrap intervals and paired comparisons |
| `make ml` | Fill-probability baselines plus two leakage checks |
| `make run-all` | Execution grid → Parquet |
| `make db-query NAME=02_benchmark_disagreement` | Run a packaged SQL query |
| `make report` | Self-contained HTML reports for the execution grid |
| `make report-all` | Also the experiment and ML reports (re-runs real work) |
| `make screenshots` | Render the dashboard to PNGs; fails if any tab raised |
| `make benchmark` | Throughput, with the machine and commit recorded |
| `make api` / `make dashboard` | HTTP API / Streamlit over the artefacts |
| `make doctor` | Engine backend and which optional extras are installed |

---

## Data

**Everything bundled is SYNTHETIC.** The generator is deterministic, requires no
network, and every artefact it produces carries
`provenance: "SYNTHETIC - deterministic generator, NOT real market data"`.

Real sources are supported through adapters, and the capability tier each one
declares is enforced rather than assumed:

| Adapter | Declares | Consequence |
|---|---|---|
| `synthetic` | `L2_MBP` | approximate queue only |
| `synthetic-mbo` | `L3_MBO` | exact FIFO queue available |
| `lobster` | `L3_MBO` | order-level, exact queue |
| `binance` | `L2_MBP` | approximate queue; depth is polled, so a `CLEAR` pulls resting orders |
| `normalized` | as declared in the file | read back what an earlier run wrote |

Requesting `EXACT_QUEUE` on L2 data raises. It does not silently degrade into a
guess, because a guess reported as an exact queue position is a fabricated
number.

---

## Known limitations

Stated here rather than discovered by a reviewer. The full list, with the reason
for each, is in [`docs/design-review.md`](docs/design-review.md).

* **Replay is counterfactual.** Our simulated orders did not alter the
  historical event stream, so our own market impact on *future* events is not
  modelled. Every result records
  `counterfactual_mode = "replay_approximation"`.
* **The impact column is a residual, not a measurement.** It absorbs the
  denominator approximation plus unmodelled impact. Measuring impact needs a
  controlled experiment or an instrumented venue; TradeForge has neither, and
  says so in every report that contains the column.
* **Queue position on L2 data is an estimate.** Three attribution policies
  (optimistic / neutral / conservative) bracket the unknown. The spread between
  them is the honest error bar, reported by
  `sql/05_queue_policy_sensitivity.sql`.
* **Latency values are scenarios, not measurements.** No hardware was measured.
  Every result records `latency_basis = "scenario"`.
* **Cancel latency is not modelled.** A passive timeout pulls the order at the
  decision instant.
* **Markouts use post-fill data by construction.** They are a measurement of what
  happened and are never reachable from a policy.

---

## Documentation

| Document | Contents |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Layers, dependency rules, why each boundary exists |
| [`docs/design-review.md`](docs/design-review.md) | What was rejected and why; known limitations |
| [`docs/event-model.md`](docs/event-model.md) | The normalized event and the adapter contract |
| [`docs/data-capabilities.md`](docs/data-capabilities.md) | Capability matrix and the refusal semantics |
| [`docs/adr/`](docs/adr) | Four architecture decision records |
| [`docs/guides/`](docs/guides) | Getting started, data, execution, TCA, extending |
| [`docs/interview-notes.md`](docs/interview-notes.md) | 120 questions the design must survive |

---

## Requirements

Python ≥ 3.11. The C++ core needs a C++20 compiler and CMake ≥ 3.20; it is
optional, and `make doctor` reports which backend is active.

The Makefile uses whatever `python` resolves to. To use a virtual environment
without activating it, pass the interpreter explicitly:

```bash
make demo PY=.venv/bin/python
```

If that interpreter cannot import the dependencies, `make` says so in one
sentence rather than printing a traceback.

```
pytest         322 test functions in six suites: unit, integration, property,
               leakage, architecture, differential
ruff + mypy    lint and type check, both clean. `make typecheck` and
               `make lint` are the commands CI runs; both pass locally too.
hypothesis     property-based invariants on the book, the bootstrap and the split
```

## License

MIT. See [`LICENSE`](LICENSE).

**Not investment advice.** This is research infrastructure. It contains no
trading system, no signal, and no claim that any strategy shown here would be
profitable on any real venue.
