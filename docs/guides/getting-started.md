# Getting started

Nothing here downloads data, calls an API or needs a network connection. The
bundled generator produces a deterministic session, so the first command you run
produces the same numbers as the last person who ran it.

## Install

```bash
python -m pip install -e ".[dev]"
make doctor
```

`make doctor` reports two things that change what every result means:

```
Configuration
  book: ok
  costs: ok
  ...

Optional dependencies
  pyarrow: available (Parquet output)
  duckdb: available (SQL layer)
  sklearn: available (ML baselines)
  streamlit: MISSING (dashboard unavailable)
  fastapi: available (HTTP API)

C++ core
  compiled core 'tradeforge_core' is not importable; running the Python
  reference implementation. Build it with `make build-cpp`.
  backend: python-reference
```

The Python backend is complete. The C++ core is a throughput optimisation and an
independent implementation to test against, not a prerequisite.

## The first three commands

```bash
make demo          # four algorithms, two styles, one cost table
make report        # self-contained HTML reports in artifacts/reports/
make db-query NAME=02_benchmark_disagreement
```

`make demo` prints all four algorithms in both styles. The first two rows, with
the `participation` column dropped for width:

```
policy       style       fill     maker   vs_arrival_bps  vs_vwap_bps  is_bps
-----------  ----------  -------  ------  --------------  -----------  -------
twap         passive     100.00%  96.3%   -7.9369         -0.8977      -7.9369
twap         aggressive  100.00%  5.1%    -6.9336         0.1063       -6.9336
```

**Stop and look at the two cost columns.** Passive TWAP beats both benchmarks;
aggressive TWAP beats arrival but trails interval VWAP. The arrival-to-VWAP
price gap is about 7.04 bps on this synthetic session. Arrival credits the
execution with market drift during the window, so neither cost alone measures
the scheduling policy's skill.

If you take one thing from this repository, take that.

## Run one execution and read the whole report

```bash
python -m tradeforge.interfaces.cli run --policy twap --style passive
```

```
metric                          value
------------------------------  -----------------------------
policy                          twap
side                            BUY
fill_ratio                      1
participation_rate              0.0189
cost_vs_arrival_bps             -7.9369
cost_vs_vwap_bps                -0.8977
implementation_shortfall_bps    -7.9369
maker_fill_ratio                0.9634
queue_mode                      APPROXIMATE
latency_basis                   scenario
counterfactual_mode             replay_approximation

Benchmarks (ticks)
benchmark        value    basis
---------------  -------  ----------------------------------
arrival_mid      10000    first mid at/after window start
interval_vwap    9992.96  volume weighted over 8417 prints
interval_twap    9992.93  time weighted mid, trapezoidal
terminal_mid     9976.5   last mid inside the window

Cost attribution (bps, positive = worse)
component                bps
-----------------------  ---------
is_filled                -7.9369
spread_cost              -0.479944
fees                     -0.0877492
timing                   -7.45728
residual (unexplained)   0.0880677
opportunity_cost         -23.5
is_total                 -7.9369

Markouts (bps, positive = adverse)
horizon_s  measurable  of_fills  volume_weighted  median
---------  ----------  --------  ---------------  -------
1          206         206       -0.4389          -0.5003
10         206         206       -0.4884          -0.5003
60         206         206       0.028             -0.5003
300        205         206       4.8054           1.0005
```

Four things worth noticing:

1. `queue_mode` is `APPROXIMATE`. On L2 data the queue position is not
   observable, so the number is an estimate. The report says so at the top, not
   in a footnote.
2. The attribution closes exactly: `is_filled` equals the sum of the four
   components. `residual` is labelled *unexplained* and is small here (0.09 bps).
   When it is large the report says the decomposition is not describing the
   execution.
3. `opportunity_cost` is −23.5 bps and contributes nothing, because the fill
   ratio is 1.0. On a partial fill it would not be free.
4. **The markouts turn.** −0.44 bps at one second, +4.81 bps at five minutes.
   Passive fills are adversely selected at longer horizons, and that is invisible
   in an average fill price.

## Run the experiment grids

```bash
make research
```

Each cell is run on identical seeds, so the comparison is paired: VWAP and TWAP
in repetition 3 see exactly the same market. Differences are therefore not
contaminated by session variance, which is what lets three sessions say anything
at all.

The output prints intervals, not just means, and applies Holm-Bonferroni across
the family of comparisons. With three seeds, nothing is significant, and the
report says so.

## Build the C++ core (optional)

```bash
make build-cpp
make doctor          # backend: cpp-core
make test-differential
```

The differential suite feeds the same event stream to both implementations and
compares the top-of-book snapshot, the level size at every traded price, the
matching result across sides, sizes and time-in-force, and the exception raised
for invalid events. If the two drift, the build fails.

## Where to go next

| Guide | Contents |
|---|---|
| [data.md](data.md) | Adapters, capability tiers, the validator |
| [execution.md](execution.md) | Policies, placement, the simulator's rules |
| [tca.md](tca.md) | Benchmarks, attribution, markouts, how to read them |
| [extending.md](extending.md) | Adding an adapter, a policy, a metric |
| [../design-review.md](../design-review.md) | What was rejected, and what is still missing |
