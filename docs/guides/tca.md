# Transaction cost analysis

## Start with the benchmark, not the cost

A cost number is meaningless without its benchmark, and the choice of benchmark
is the largest single decision in the analysis. The bundled data makes this
concrete:

| Benchmark | Price (ticks) | Cost (bps) | Interpretation |
|---|---|---|---|
| arrival mid | 10 000.0 | −7.94 | market drift during the window |
| interval VWAP | 9 992.96 | −0.90 | compared with traded volume's average price |
| interval TWAP | 9 992.93 | −0.87 | compared with the time-weighted mid |
| interval mid | 9 992.99 | −0.92 | compared with the unweighted mid |
| terminal mid | 9 976.5 | +15.60 | — |

Same execution, five costs spanning 23.54 bps. The market fell 23.5 bps from
arrival to terminal mid; the interval VWAP was about 7.04 bps below arrival.
An aggressive TWAP run changes sign between arrival (−6.93 bps) and
interval VWAP (+0.11 bps).

```bash
make db-query NAME=02_benchmark_disagreement
```

`arrival_minus_vwap_bps` is the signed gap between costs. When its magnitude is
large, a claim such as "we beat the arrival price by X bps" needs to be read
alongside the market's movement during the window.

## Sign convention

**Positive bps is worse.** Applied through `Side.sign`, so a BUY below the
benchmark and a SELL above it both report negative. A table of these numbers can
be averaged, compared and bootstrapped without anyone flipping a sign by hand.

## Implementation shortfall

Perold's definition, in `tca/metrics.py`:

```
IS_total = IS_filled * fill_ratio  +  opportunity_cost * (1 - fill_ratio)
```

The filled leg is measured against the arrival price; the unfilled leg is charged
at the end-of-window price. Both legs are weighted by their share of the
**requested** quantity, so a strategy that fills 10% cheaply does not look good.
If a required leg is unmeasurable, total shortfall is `None`, not zero.

`opportunity_cost_bps` is the unfilled leg on its own. On a full fill it
contributes nothing; on a partial fill it is often the dominant term, which is
why a fill-ratio column always sits next to it.

## Attribution

The decomposition is **additive by construction**:

```
is_filled_bps = spread_cost_bps + fees_bps + timing_bps + residual_impact_bps
```

| Component | Meaning | Sign |
|---|---|---|
| `spread_cost_bps` | crossing the spread, signed against the mid before each fill | negative for maker fills, which earn it |
| `fees_bps` | exchange fees + commission, net of maker rebates | negative when rebates exceed fees |
| `timing_bps` | how far the mid drifted between arrival and each fill | market drift, not a decision error |
| `residual_impact_bps` | **unexplained** | see below |

The residual is a residual, and it is labelled as one in every report and every
SQL column. It absorbs two things:

1. the difference between using `mid_at_fill` and `arrival` as denominators — a
   second-order approximation error;
2. genuine market impact, which this platform **does not claim to identify**,
   because in a replay our orders never altered the tape.

`residual_share` is reported so a large unexplained share is visible rather than
hidden behind four tidy columns. When it exceeds roughly 0.5, the decomposition
is not describing the execution and should not be quoted.

```bash
make db-query NAME=03_cost_attribution
```

## Markouts

A markout measures where the mid went *after* our fill. **Positive is adverse.**

| Horizon | Volume-weighted markout |
|---|---|
| 1 s | −0.44 bps |
| 10 s | −0.49 bps |
| 60 s | +0.03 bps |
| 300 s | **+4.81 bps** |

That reversal is adverse selection. At short horizons the passive fill earned the
spread; at five minutes the market had moved against the position. It is invisible
in an average fill price, and it is the main reason a passive strategy can look
cheap on spread and expensive on markout.

Fills whose horizon extends past the recorded data are counted as *not
measurable* and excluded. They are never imputed as zero: a missing markout and a
flat markout are different facts. The `coverage` column reports how many fills
were usable.

Markouts use post-fill data by construction. That is legitimate — they are a
measurement of what happened — and they are never reachable from a policy.

```bash
make db-query NAME=04_markout_adverse_selection
```

## Statistics

**Intervals, not point estimates.** Every aggregate carries a resampled interval
with its method, block length, resample count and observation count attached, so
a reader can judge how much the interval is worth.

**Block bootstrap within a session.** Execution costs are autocorrelated:
volatility clusters, spreads widen together, a liquidity drought persists across
many executions. Resampling individual observations destroys that structure and
produces intervals that are far too narrow. `tests/unit/test_research.py` checks
this by comparing the block interval against the i.i.d. one on an AR(1) series —
the block interval must be wider.

**Session bootstrap across sessions.** Each synthetic session is an independent
draw, so the resampling unit is one session and the block length is 1. Using
blocks there would not add information; it would only reduce the effective
sample size. `research/runner.py` documents which unit applies where.

**Pairing by seed.** Two strategies run on the same seed see the same market, so
the difference between them is not contaminated by session variance. Pairing is
what lets three sessions say anything at all. Unusable pairs are dropped
together, never one-sided.

**Correction for searching.** `apply_holm` adjusts p-values across the whole
family of comparisons in one experiment, and the raw values are reported
alongside so the cost of searching is visible. With eight comparisons at
α = 0.05, one "significant" result is what noise alone produces.

## What the numbers do not say

* **The impact column is not an impact measurement.** See above.
* **Queue position is an estimate on L2 data.** The spread across the three
  attribution policies is the honest error bar; see
  `sql/05_queue_policy_sensitivity.sql`.
* **Latency is a scenario.** No hardware was measured.
* **The replay is counterfactual.** Our orders did not alter the event stream, so
  our own impact on future events is unmodelled.
* **The data is synthetic.** Numbers describe the simulator's behaviour on a
  deterministic generator, not any real venue.

## Building a report

```bash
make report                       # artifacts/reports/*.html
python -m tradeforge.interfaces.cli run --policy pov --style passive --json
```

The HTML reports are single files with no JavaScript framework and no CDN: they
open from disk and render offline. Provenance and caveats come **before** any
number, which is the point — a cost figure without its queue mode, latency basis
and counterfactual mode is not interpretable.
