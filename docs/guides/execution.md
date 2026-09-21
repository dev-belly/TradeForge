# Execution: policies, placement and the simulator

## The five rules the simulator enforces

All of them live in `execution/simulator.py`. They are stated here because each
one exists to prevent a specific way a backtest lies.

**1. A decision at `t` reaches the venue at `latency.arrival_ns(t)`.** Between
those instants the market keeps moving and every intervening event is processed.
Nothing sleeps; latency advances the simulation clock. An architecture test bans
`time.sleep` from the simulation path.

**2. An aggressive order pays the arrival-time book against a limit fixed at
decision time.** That asymmetry is the entire point of modelling latency: if the
touch moves away while the order is in flight, the order does not fill. Setting
the limit at arrival time instead would make latency free.

**3. A passive order joins the back of the queue at the arrival-time touch, and
only a trade that consumes everything ahead of it fills it.** There is no code
path in which "best bid >= our price" produces a fill.

**4. Simulated fills do not remove liquidity from the replayed book.** Our orders
were not in the historical stream, so removing depth would double-count it.

**5. Trades with an unknown aggressor are skipped, not attributed.** From an
unattributed print we cannot say which side was consumed. The count is reported
in `metadata["unattributed_trade_events"]`.

## Policies

| Policy | Schedule | Characteristic failure |
|---|---|---|
| `twap` | equal quantity in equal time slices | ignores volume entirely |
| `vwap` | proportional to a **prior-session** volume profile | needs history; a bad profile front-loads the wrong hours |
| `pov` | a fixed fraction of **observed** volume | barely trades in quiet periods |
| `is_baseline` | Almgren–Chriss front-loading | a simplified trajectory, not a calibrated model |

All four share one rule for the remainder:

```
cumulative target after slice k  =  sum(weights[:k])
outstanding                      =  filled + in-flight
slice quantity                   =  clamp(target - outstanding, 0, parent remaining)
```

which handles missed slices, lot rounding and late fills without special cases.

### VWAP refuses to guess

```python
VwapPolicy(parent=..., spec=..., oms=..., n_slices=30, profile=None)
# LeakageError: VWAP policy needs a historical volume profile; none was provided.
```

It raises **at construction**, not at the first slice. A missing profile is a
configuration error, and discovering it part-way through a replay wastes the run
and looks like a market event.

The harness supplies profiles from *other seeds*, i.e. other sessions. Using the
current session's realized volume curve to schedule that same session is
future leakage and is rejected.

### Almgren–Chriss units

`kappa = sqrt(lambda * sigma^2 / eta)` has units of 1/time, so it must multiply a
time in the same unit. The policy measures the horizon in `time_unit_ns`
(default one second) and reports the dimensionless driver `kappa * T`:

```bash
python -c "
from tradeforge.execution.policies import ImplementationShortfallPolicy
# with configs/execution.yaml defaults and a 30-minute window, kappa*T ~ 1.27
"
```

`kappa * T -> 0` recovers TWAP. Evaluating the ratio through a numerically stable
branch avoids `math.range_error` when a risk-averse configuration is used: for
large `kappa * T` the ratio converges to `exp(-kappa * t)`, which is what the
code returns rather than calling `sinh` on a huge argument.

## Placement styles

| Style | Behaviour at arrival |
|---|---|
| `passive` | join the back of the queue at the touch (offset by `passive.price_offset_ticks`) |
| `aggressive` | cross the spread with a limit fixed at the decision-time touch, IOC by default |
| `adaptive` | passive when the spread is at least `adaptive_min_spread_ticks`, otherwise cross |

A passive order that rests past `passive.timeout_ns` is pulled. The unfilled
quantity is not lost: it returns to the parent's remaining pool and the next
slice picks it up through the cumulative-target rule. **Cancel latency is not
modelled** — the pull happens at the decision instant.

## End of window

When the clock reaches `parent.end_ns`, the simulator:

1. cancels every working child order, so the policy sees an accurate remainder;
2. calls `policy.on_window_end(state)` **exactly once**.

The order matters. Cancelling first is what makes the sweep correct; computing it
against quantity that is about to be pulled would double-count.

| `end_of_window` | Result |
|---|---|
| `sweep_marketable` | cross with everything still unfilled |
| `cancel` | pull working orders; the remainder is charged as opportunity cost |
| `leave` | leave working orders in the book |

The slice that would land exactly on `end_ns` is deliberately not emitted: the
sweep covers that quantity, and emitting both would count it twice.

## Guardrails

Not a production risk system. They exist so an experiment cannot silently produce
nonsense — a 90% participation run, a 10× size typo — and so the replay
approximation stays inside the range where it is defensible.

```yaml
guards:
  max_child_order_base: 25000    # must exceed parent_order.quantity_base
  max_notional: 5000000.0
  max_participation: 0.10
  max_open_orders: 25
  max_inventory_base: 100000
  kill_switch_enabled: true
```

`max_child_order_base` must exceed the parent size, or the end-of-window sweep is
rejected and the parent silently finishes unfilled. The bundled config sets it to
25 000 against a 20 000 parent, so a 10× typo still trips but a legitimate sweep
does not.

A tripped guard cancels everything and stops trading for the remainder of the
run; the reason is recorded in `metadata["guard_breaches"]`.

## Reading the result

```python
context = harness.run(RunRequest(policy="twap", style="passive"))
result = context.result

result.completion_rate        # filled / requested
result.maker_fill_ratio       # share of filled quantity that earned the spread
result.avg_fill_price_ticks   # quantity-weighted
result.participation_rate     # our filled quantity / market volume
result.fills                  # every fill, with liquidity flag and queue wait
result.child_orders           # every order, with its placed price and estimated queue ahead
result.queue_mode             # APPROXIMATE or EXACT
result.latency_basis          # scenario or observed
result.counterfactual_mode    # always replay_approximation
```

`avg_fill_price_ticks` alone says nothing. It must be read against a benchmark,
which is what the TCA layer does — see [tca.md](tca.md).
