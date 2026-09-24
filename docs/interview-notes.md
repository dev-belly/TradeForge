# 120 questions this design has to survive

Answers are grounded in the code, not in intent. Where something is not done,
the answer says so and says why. Where a number is quoted it is reproducible from
the bundled synthetic data.

---

## A. Design and architecture (1–22)

**1. Why integer ticks instead of floats for prices?**
Comparing floating-point prices for equality is the most common way a matching
engine produces impossible fills. `0.1 + 0.2 != 0.3` becomes "the level was not
found" or "the limit was not crossed". `PriceTicks` is `int64` throughout the
core; conversion to `Decimal` happens only at reporting boundaries, through
`InstrumentSpec`, so there is exactly one place the rule can be violated.
ADR-001.

**2. Where is the price→ticks conversion allowed?**
`InstrumentSpec.price_to_ticks` and `ticks_to_decimal`. `ticks_to_float` exists
and is documented as lossy, for plotting only, never fed back into book or
matching logic.

**3. How do you stop the strategy from touching simulator internals?**
Structurally, not by convention. A policy receives `MarketState` and returns
`ChildOrder`s. It has no reference to the book, the clock, the queue model, the
OMS or the event stream. There is no code path by which it could reach them.
`MarketState` is a frozen dataclass containing only present and past-derived
quantities — no field can carry a future event or a stream handle, and a test
asserts that.

**4. Why is the domain not allowed to import anything outward?**
The domain is the specification. If it imported the application layer, the
dependency would be circular in meaning: the thing being specified would depend
on the thing implementing it. `tests/architecture/test_layers.py` walks the AST
and fails on a violation, and `test_no_import_cycles` fails on any cycle.

**5. Is the layering actually enforced, or is it a diagram?**
Enforced. Six parametrised architecture tests run over every module: import
direction per layer, no framework outside `interfaces`, no optional dependency in
the core, no cycles, file length ≤ 600, function length ≤ 100, no star imports, no
`time.sleep` in the simulation path, no `engine.py`/`utils.py` catch-all names.

**6. Why is `utils.py` banned?**
A module named for what it is not is a module nobody can reason about. Naming
forces the responsibility question. The test allows an exception at the package
root only if someone argues for it in review.

**7. What happens when you need to add a framework?**
It goes in `interfaces/`. FastAPI, Streamlit, Typer and Rich are all there, and
`test_no_framework_in_core` fails if one leaks inward. The core installs and its
tests pass with the standard library plus NumPy.

**8. Why six protocols instead of abstract base classes?**
`Protocol` keeps the domain free of inheritance hierarchies and lets
infrastructure implementations stay framework-agnostic. `BookProtocol`,
`MarketDataAdapter`, `ExecutionPolicy`, `QueueModel`, `LatencyModel`,
`CostModel`, `ImpactModel`, `InstrumentProvider`, `Clock` — each is a change
point and nothing else is abstracted.

**9. How many places can be swapped out?**
Those protocols. Adding a source, a policy, a queue model, a latency model, a
cost model or an impact model is one class plus one registry line. Adding
anything else means changing the design, which is the signal that it should be a
discussion rather than a commit.

**10. Why is `Clock` a protocol rather than a concrete import?**
The simulator needs a clock; the clock lives in `replay`. Importing it would make
`execution` depend on `replay`, inverting the natural direction — replay *drives*
execution. Declaring the protocol in `domain` keeps the dependency one-way. This
was caught by the architecture test, not by review.

**11. What is the single most important design decision?**
That the platform reports what it cannot know. Capability gating, the residual
labelling, the `None`-not-zero convention for unmeasurable metrics, the queue
attribution spread, the counterfactual mode on every result. The engineering is
mostly ordinary; the refusal to fabricate is the design.

**12. Why is there no "strategy" or "signal" layer?**
Because a signal layer would tempt someone to optimise against the synthetic
generator and report the result. The ML layer exists to answer "do these features
carry signal" and is explicitly not a trading strategy.

**13. What is the counterfactual mode?**
`replay_approximation`, recorded on every result. Our simulated orders did not
alter the historical event stream, so the stream we replay is the one that would
have happened without us. This is the central approximation of the whole platform.

**14. Why not simulate the impact and iterate to a fixed point?**
Because the impact model is not calibrated, so the fixed point would be a
calibrated-looking number derived from an uncalibrated one. Better to state the
approximation and report the residual as unexplained.

**15. How do you know the two implementations have not drifted?**
`tests/differential/test_python_cpp_parity.py` feeds the same 4 000-event stream
to both and compares the top-of-book snapshot every 25 events, the level size at
every traded price, matching results across 2 sides × 5 sizes × 3
time-in-force values, and the exception raised for invalid events. It runs in CI.

**16. What if the C++ core is not built?**
The differential tests skip with a message naming the build command. Everything
else runs on the Python reference. A skipped differential test is a visible gap;
a failing one on a machine without a compiler would train people to ignore it.

**17. Why keep a Python implementation at all if C++ is faster?**
Two reasons. It is the specification the C++ must agree with, and differential
testing needs two independent implementations. A single implementation can only
be tested against itself.

**18. What is the file-size limit and why 600 lines?**
A file over 600 lines is doing more than one job. The limit is enforced, and it
caught `harness.run` at 146 lines, which was then split into `_assemble`,
`_policy`, `_simulator` and `_runner`.

**19. How are configuration errors handled?**
They raise, at construction where possible. `VwapPolicy` refuses to be built
without a historical profile; `GuardConfig.from_dict` raises on a malformed
mapping. Discovering a configuration error part-way through a replay wastes the
run and looks like a market event.

**20. Where do the numbers in the README come from?**
`make demo` and `make research`, both reproducible. Every artefact carries the
config fingerprint, the seeds and the git commit; `research/registry.py` refuses
to guess a commit hash and records `unknown` instead.

**21. What would you do differently with another month?**
Add a second real adapter with a genuine L3 file, so the exact queue path is
exercised on real data rather than on the generator. The exact-queue code is
tested but not exercised end to end outside unit tests.

**22. What is deliberately missing?**
No live trading, no order gateway, no risk system, no portfolio layer, no
calibration to any venue. Each of those would need data this platform does not
have, and adding them without it would produce confident numbers with no basis.

---

## B. Market data and the book (23–44)

**23. What does "capability tier" mean?**
A claim about what a source actually contains: `L1` (top of book), `L2_MBP`
(aggregated levels), `L3_MBO` (per-order). Every feature declares the tier it
needs, and requesting more than the source declares raises
`DataCapabilityError`.

**24. Why refuse rather than degrade?**
Because a degraded result is indistinguishable from a correct one downstream. A
queue position estimated from aggregated levels and reported as exact produces a
backtest that is wrong in a direction nobody can see. A refusal is loud; a
degradation is silent.

**25. Which capabilities are tier-exact rather than "at least"?**
`EXACT_QUEUE`, `ORDER_LEVEL_CANCEL` and `MBO_RECONSTRUCTION`. The check is
deliberately not monotone: a higher tier does not grant them implicitly.

**26. How is a trade applied to an MBP book?**
Consume the **resting** side at the print price. A single print cannot reveal a
multi-level sweep, so consuming only at the print price is the honest reading.

**27. What if a trade has no aggressor side?**
In `strict` mode (the default) it raises. In `skip` mode it is recorded and no
depth is consumed, and the simulator counts it in
`metadata["unattributed_trade_events"]`. We never infer the aggressor from price
alone.

**28. Why is `MODIFY` rejected on an MBP book?**
An aggregated level has no order identity, so "modify this order" cannot be
interpreted. Accepting it would mean guessing whether the quantity change was an
add or a cancel, and on which side of the queue. The error message says to emit
`CANCEL` + `ADD` instead.

**29. How does the MBO book give an exact queue position?**
It stores a FIFO deque per price level. `queue_ahead_base(order_id)` sums the
quantities of the orders ahead of the named one. The position is *observed*, not
estimated.

**30. What happens to priority on a size-decreasing modify?**
Configurable: `LOSE_PRIORITY` (default, conservative) treats any change as losing
priority; `KEEP_PRIORITY` preserves the position for a decrease. The choice is
documented because it changes results.

**31. Why is a locked market tolerated and a crossed one not?**
A locked market (bid == ask) genuinely occurs on real venues and refusing it
would reject valid data. A crossed one (bid > ask) is always a reconstruction
failure. Locked is `WARN` by default and reported through the violation hook; a
property test asserts that a locked market is *reported*, never silently fixed by
deleting a level.

**32. Why not delete a level to resolve a crossing?**
Because deleting it changes the reconstructed book to hide a fact about the
source. The violation is reported and the operator decides.

**33. Is the snapshot handed to a policy mutable?**
No. `BookSnapshot` and `MarketState` are frozen dataclasses; mutating a snapshot
does not change the book, and mutating the book does not change an old snapshot.
Both directions are tested.

**34. What does `order_count` mean on an MBP book?**
It is always 0. The aggregated source does not reveal how many orders make up a
level, and inventing a number would be a fabrication. On an MBO book it is the
real count.

**35. How is the book kept consistent with the shadow book in the generator?**
The synthetic adapter maintains its own `_ShadowBook` and only emits events valid
against it, so no negative depth, no unknown cancels, no trades against empty
levels. That is why the strict validator reports zero issues on the generated
stream.

**36. What does the validator check?**
Timestamp ordering, sequence monotonicity, required fields per event type,
non-positive quantity, non-positive price, symbol consistency. Three modes:
`STRICT` raises, `WARN` records and passes, `REPAIR` fixes only what the event
type requires and the source omitted, and flags the event.

**37. Can `REPAIR` invent a price?**
No. It fills in a value the event type requires and the source omitted, and marks
the event with `EventFlag.REPAIRED`. It never invents a price or a side.

**38. Why does `receive_timestamp_ns` matter?**
It is the only thing that makes the `observed` latency model a measurement rather
than a scenario. It is never synthesized, because a fabricated one would let the
platform report a scenario as observed.

**39. What is the ordering key for events?**
`(exchange_timestamp_ns, sequence_id)`. Same-timestamp events keep their source
order, which is what makes a replay deterministic across machines.

**40. How does the book handle a `CLEAR`?**
Both sides are emptied. On a polled L2 feed that means the queue position becomes
unknowable, and the simulator pulls resting passive orders and counts them.

**41. Why is that the right behaviour for a polled feed?**
Because it is true. A polled snapshot re-states the level contents; it does not
update them, so our position within the level cannot be tracked. On a stream that
clears every second, passive execution becomes nearly useless — which is the
honest answer about polled data.

**42. Which metrics are computed from the book?**
Mid, quoted spread, relative spread in bps, microprice, top imbalance,
multi-level imbalance, depth slope, depth base. Each returns `None` rather than
zero on an empty or one-sided book.

**43. What is the microprice and why is it in the core?**
The size-weighted mid, `(bid*ask_qty + ask*bid_qty) / (bid_qty + ask_qty)`. A
property test asserts it stays inside the spread, because it is a convex
combination and leaving the quotes would mean a bug.

**44. How is `depth_slope` defined?**
The fitted slope of cumulative depth against price distance from the touch over
the tracked levels. Positive when depth accumulates away from the touch, which is
the normal shape.

---

## C. Queue modelling (45–58)

**45. What is the queue model for?**
To decide when a resting passive order fills. It is the difference between "the
price touched our level" and "the market consumed everything ahead of us and then
reached us".

**46. Why is "price touched therefore filled" wrong?**
Because it assumes we are at the front of the queue. A passive order joins the
back of a level, so a trade must consume the whole level ahead of it before
touching us. Filling on touch would make passive execution free and instant, and
the entire passive/aggressive cost difference would vanish.

**47. Is there a test for that?**
Yes, and it is the one the design is built around:
`tests/unit/test_queue.py::test_price_touch_alone_never_fills`. It registers an
order with 800 shares ahead, does nothing, and asserts `fillable_base == 0`.

**48. What does the approximate model assume?**
That our order joins at the back of the displayed level, and that a trade
consumes the queue ahead before reaching us. Both are stated assumptions, and
both are wrong in a direction that matters when the level is mostly hidden
orders.

**49. How does it handle a level shrinking without a trade?**
With one of three policies, because the answer is unobservable from L2 data:

| Policy | Assumption |
|---|---|
| `optimistic` | the removed shares were **behind** us — we move up the queue |
| `neutral` | split in proportion to our visible share |
| `conservative` | the removed shares were **ahead** of us — we move back |

**50. Which is right?**
Unknown, and that is the point. The spread between the three is the honest error
bar on any passive-execution claim made from L2 data.
`sql/05_queue_policy_sensitivity.sql` reports it as `policy_spread_bps`.

**51. What if that spread is larger than the difference between two strategies?**
Then the strategies are not distinguishable at this data tier, whatever the point
estimates say. The query is designed to make that comparison possible.

**52. What does the exact model do differently?**
It requires L3 data and takes a list of `(order_id, quantity)` pairs ahead of us.
Consumption is named by order id, so a partial execution of the front order is
attributed precisely. `on_size_decrease` raises if `order_id` is missing, rather
than falling back to a guess.

**53. Why does the exact model refuse L2 data at construction?**
Because an "exact" queue built from aggregated levels is a fabricated claim. The
refusal is at construction rather than at first use, so a misconfiguration fails
immediately.

**54. How is the queue notified during replay?**
Before the event mutates the book, because the model needs the level size as it
was when the trade or cancel arrived. The runner's order is: advance clock →
drain arrivals → notify queue → apply to book → features → policy.

**55. Can a trade on the wrong side consume our queue?**
No. `_positions_at(price, side)` filters by side, and a test asserts that a print
against the bid does not consume queue on the ask. This was a real bug found
during development.

**56. Does exhausting the queue fill us?**
Not by itself. A trade of exactly the queue-ahead quantity clears the level but
has already been fully allocated. Reaching us requires quantity *beyond* the
queue. There is a dedicated test for this distinction.

**57. How is queue wait reported?**
`Fill.queue_wait_ns`, non-negative, and asserted so. The distribution is a direct
observable of whether the queue model is behaving: if the estimated queue ahead
is wrong, the wait distribution is where it shows up.
`sql/06_fill_quality_by_liquidity.sql` reports mean and max.

**58. What is the queue wait on the bundled data?**
Mean 8.8 seconds, max 24.9 seconds for maker fills. Reproducible with
`make run-all` then `make db-query NAME=06_fill_quality_by_liquidity`.

---

## D. Execution and simulation (59–80)

**59. How is latency modelled?**
By scheduling the order's arrival on the simulation clock at
`latency.arrival_ns(decision_ns)`. Market events keep flowing in between and are
all processed. Nothing sleeps — an architecture test bans `time.sleep` from the
simulation path.

**60. Why is that better than sleeping?**
Sleeping makes a latency experiment measure wall-clock time and depend on the
machine. Advancing a clock makes it measure *market* time, which is the thing
that matters: the question is how much the market moves while the order is in
flight, not how long the program waited.

**61. Where is an aggressive order's limit price set?**
At **decision** time — the touch when the policy decided. The fill happens
against the **arrival**-time book. That asymmetry is the whole point: if the
touch moves away while the order is in flight, it does not fill.

**62. What would happen if the limit were set at arrival time?**
Latency would be free. The order would always cross at whatever the book happens
to be when it lands, which is precisely the advantage a fast participant has.

**63. Is the latency value a measurement?**
No. `latency_basis` is `scenario` on every bundled result. No hardware was
measured, and the platform says so. `observed` is only reported when the source
provides a receive timestamp.

**64. What are the four algorithms?**
TWAP (equal quantity in equal time), VWAP (proportional to a prior-session volume
profile), POV (a fraction of observed volume), and an Almgren–Chriss baseline
(front-loaded by risk aversion).

**65. Why does VWAP need prior sessions?**
Because scheduling against the current session's realized volume curve is future
leakage: the schedule would know when the volume was coming. `VwapPolicy` raises
`LeakageError` at construction without a profile, and the harness builds one from
*other seeds*.

**66. What is the shared remainder rule?**
`slice quantity = clamp(cumulative_target - (filled + in_flight), 0, parent_remaining)`.
It handles missed slices, lot rounding and late fills without special cases.

**67. What happens at the end of the window?**
The simulator cancels every working child order **first**, then calls
`policy.on_window_end(state)` exactly once. Cancelling first is what makes the
sweep correct: computing it against quantity that is about to be pulled would
double-count it.

**68. Why is the final scheduled slice not emitted?**
Because it would land exactly on `end_ns`, where the sweep also fires. Emitting
both would count that quantity twice. The sweep covers it.

**69. What are the end-of-window modes?**
`sweep_marketable` (cross with everything unfilled), `cancel` (pull working
orders; the remainder is opportunity cost), `leave` (leave them in the book).

**70. What is `unfilled_base` and why does it differ from `parent_remaining_base`?**
`parent_remaining_base` subtracts in-flight quantity; `unfilled_base` does not.
The sweep uses `unfilled_base` because the working orders have just been pulled,
so the in-flight quantity is no longer in flight.

**71. What do the guardrails do?**
They stop an experiment from silently producing nonsense: a child order larger
than the configured maximum, a notional beyond the limit, a participation rate
above the cap, too many open orders, or projected inventory beyond the limit.

**72. Why must `max_child_order_base` exceed the parent size?**
Otherwise the end-of-window sweep is rejected and the parent silently finishes
unfilled. The bundled config sets 25 000 against a 20 000 parent, so a 10× typo
still trips but a legitimate sweep does not. This was a real bug found in
development: a 16 949-share sweep was rejected by a 10 000 limit.

**73. Are the guardrails a risk system?**
No, and the module docstring says so. They exist for experiment hygiene.

**74. What is a passive timeout for?**
Cancel/repost. An order resting past `passive.timeout_ns` is pulled, and the
unfilled quantity returns to the parent's remaining pool for the next slice.

**75. Is cancel latency modelled?**
No. The pull happens at the decision instant. This is listed in the known
limitations.

**76. What does `PlacementStyle.ADAPTIVE` do?**
Posts passively when the spread is at least `adaptive_min_spread_ticks`, otherwise
crosses. A minimal, documented rule rather than a claim about optimal placement.

**77. How does an aggressive order that does not fully fill behave?**
With `IOC` or `FOK` the remainder is cancelled. With `DAY`/`GTC` it rests at its
limit price and joins the queue, so it can subsequently earn maker fills. That is
why `twap/aggressive` reports 5.1% maker fills rather than zero.

**78. Why does `maker_fill_ratio` differ so much between styles?**
Passive: 96.3%. Aggressive: 5.1%. The passive orders rest and wait for trades to
reach them; the aggressive orders cross and are done. The small maker share in
the aggressive case comes from DAY remainders.

**79. What does `is_baseline` do differently?**
It front-loads: with `kappa * T ≈ 1.27` on the bundled config, it trades more
than TWAP in the first half of the window. There is an integration test asserting
exactly that.

**80. Why is the last slice's quantity `round_to_lot`-ed down?**
Because the instrument's lot size is a real constraint. The remainder is not
lost — the cumulative-target rule gives it to the next slice, and ultimately to
the sweep.

---

## E. TCA (81–98)

**81. What is the most important idea in the TCA layer?**
That the benchmark choice can flip the sign of the conclusion. On the bundled
synthetic data, aggressive TWAP costs −6.93 bps against arrival and +0.11 bps
against interval VWAP. Passive TWAP costs −7.94 and −0.90 bps respectively.
The market fell 23.5 bps from arrival to terminal mid, while interval VWAP
was about 7.04 bps below arrival. Report both benchmarks.

**82. Why not just use arrival price?**
Because it is the most flattering benchmark for any strategy that benefits from
drift, and drift is not skill. It is still reported — it is a legitimate
benchmark — but never alone.

**83. What is the sign convention?**
Positive bps is worse, applied through `Side.sign`, so a buy below the benchmark
and a sell above it both report negative. A table of these numbers can be
averaged and bootstrapped without anyone flipping a sign by hand.

**84. What is implementation shortfall?**
Perold's definition: `IS_total = IS_filled × fill_ratio + opportunity_cost × (1 − fill_ratio)`.
The filled leg is measured against arrival; the unfilled leg is charged at the
end-of-window price. Both weighted by share of the **requested** quantity, so a
strategy that fills 10% cheaply does not look good.

**85. Why does that matter?**
Because the naive version — measuring only what filled — rewards a strategy for
failing to execute. On the bundled data, `opportunity_cost_bps` is −23.5; on a
full fill it contributes nothing, but on a partial fill it is often dominant.

**86. What is in the cost attribution?**
`is_filled = spread_cost + fees + timing + residual_impact`, additive by
construction.

**87. What is the residual?**
The part the decomposition does not explain. It absorbs the difference between
using `mid_at_fill` and `arrival` as denominators (a second-order approximation
error) and any genuine market impact. It is labelled "unexplained" in every
report and every SQL column.

**88. Why not call it market impact?**
Because measuring impact requires either a controlled experiment or an
instrumented venue. In a replay our orders never altered the tape, so we cannot
identify our own impact causally. Calling the residual "impact" would be the
single most dishonest thing this platform could do.

**89. How do you know the residual is not hiding something?**
`residual_share` is reported. On the bundled TWAP/passive run it is −1.1%, so the
components carry the story. When it exceeds roughly 0.5, the report says the
decomposition is not describing the execution and should not be quoted.

**90. Why can `spread_cost_bps` be negative?**
Because maker fills *earn* the spread rather than paying it. Measuring it per
fill, signed against the mid before the fill, is what makes the maker/taker
economics visible instead of assuming a half-spread cost.

**91. Why is the mid reference taken strictly *before* the fill?**
So the mid of the very event that produced the fill cannot leak into its own
cost. `MarketObserver.mid_strictly_before` uses `bisect_left`.

**92. What is a markout?**
Where the mid went *after* the fill. Positive is adverse: the mid moved against
us.

**93. What do the bundled markouts show?**
−0.44 bps at 1 s, −0.49 at 10 s, +0.03 at 60 s, **+4.81 at 300 s**. The reversal
is adverse selection: the passive fill earned the spread and then the market
moved against the position. Invisible in an average fill price.

**94. How are unmeasurable markouts handled?**
Excluded and counted. `n_measurable` and `coverage` are reported. They are never
imputed as zero, because a missing markout and a flat markout are different
facts.

**95. How does the interval VWAP avoid being a full-day VWAP?**
It is computed only from trade prints inside the execution window, and the
observer's `start_ns`/`end_ns` bound it. A full-day VWAP used for a same-day
execution is a leakage error the VWAP *policy* explicitly refuses.

**96. How is the interval TWAP computed?**
Trapezoidal time-weighted average of the mid. A plain mean of observations would
over-weight busy periods, because more events arrive when the book is active —
there is a test that constructs exactly that case and asserts the time-weighted
value is not dragged toward the busy price.

**97. What does the report show first?**
Provenance and caveats, before any number. A cost figure without its queue mode,
latency basis and counterfactual mode is not interpretable, and burying those in
a footnote is how they get ignored.

**98. Are the HTML reports dependent on anything?**
No. Single file, no JavaScript framework, no CDN, no build step. They open from
disk and render offline. A report that needs a network connection to render is a
report that will one day fail in front of the person who needed it.

---

## F. Statistics and machine learning (99–114)

**99. Why a block bootstrap and not i.i.d.?**
Execution costs are autocorrelated: volatility clusters, spreads widen together,
a liquidity drought persists across many executions. Resampling individual
observations destroys that structure and produces intervals that are far too
narrow. `test_block_bootstrap_is_wider_on_autocorrelated_data` builds an AR(1)
series and asserts the block interval is wider.

**100. When is i.i.d. resampling correct then?**
When the resampling unit is an independent session. Across seeds there is no
block structure to preserve, so the unit is one session and the block length is
1. Using blocks there would not add information; it would only reduce the
effective sample size. `research/runner.py` documents which applies where.

**101. Why pair by seed?**
Two strategies on the same seed see the same market, so the difference between
them is not contaminated by session variance. Pairing is what lets three sessions
say anything at all. Unusable pairs are dropped together, never one-sided —
`paired_comparison` raises on unequal lengths with the message "Pair by seed, not
by convenience".

**102. Why a sign test rather than a t-test?**
Execution costs are heavy-tailed and bounded by nothing. A t-test on 30 paired
observations would be a distributional assumption that cannot be defended. The
sign test needs only the direction.

**103. What did the algorithm comparison find?**
Nothing significant. VWAP versus TWAP: mean difference +0.013 bps, 95% interval
[−0.001, +0.037], sign-test p = 1.000. The platform reports that rather than
quoting the point estimate.

**104. Do you correct for multiple comparisons?**
Yes. `apply_holm` adjusts across the whole family in one experiment, and the raw
p-values are reported alongside so the cost of searching is visible. With eight
comparisons at α = 0.05, one "significant" result is what noise alone produces.

**105. Why does every interval carry its method and block length?**
So a reader can judge how much the interval is worth. An interval without its
assumptions is a number pretending to be a measurement.

**106. How is the ML target defined?**
`fill_within_horizon`: if we post a passive order at the current touch now, does
the queue ahead of us get consumed within 30 seconds?

**107. What is the label's stated assumption?**
That cancels sit **behind** our hypothetical order, so only trades consume the
queue. That is the conservative reading. L2 data cannot distinguish cancels ahead
from cancels behind, and the assumption is stated in the dataset report.

**108. How is the split done?**
Time ordered, never shuffled, with a purge at each boundary derived from the
label horizon: `ceil(horizon_ns / sample_interval_ns)`, floored at the configured
`embargo_ns`. `assert_disjoint` verifies each sample belongs to at most one
split.

**109. Why is the embargo in nanoseconds and not events?**
Because samples-per-event depends on the sampling interval. A value that looks
harmless — "100 events" — is 100 samples at a 5-second interval, i.e. 500
seconds, which wiped out the entire validation split during development. The
unit is now nanoseconds, and `assert_splits_usable` raises if an embargo leaves
any split below 5% of the data.

**110. How is the scaler fitted?**
On the training split only, then frozen. Fitting it on the whole dataset is one
of the most common silent leaks in applied ML. Missing values are imputed with
the **train** column mean, not the test mean.

**111. What are the two leakage checks?**
A **label-shuffle canary** and a **feature leak screen**, and they catch
different things.

**112. What does the canary catch?**
A fixed decision rule presented as a fitted model (shuffling labels cannot change
a rule that ignores them), and preprocessing that carries label information from
train to test. It fits on shuffled training labels and scores on the real test
labels.

**113. What does the canary *not* catch?**
A raw feature that encodes the label. A linear model fitted to noise shrinks its
coefficients toward zero and collapses to the base rate, so the canary passes
while the real metrics are inflated. That is why the feature screen exists: it
tests each raw column univariately at a 0.90 AUC bar
(`TestFeatureLeakScreen`). The canary test originally asserted it would fire on
such a leak — it did not, so the screen was added rather than the test being
weakened.

**114. What did the ML baselines find?**
No baseline's edge over the base rate is distinguishable from noise. The target
has a base rate of 0.85 on the test split. Ridge regression scores 0.8585 — a
lift of +0.94 percentage points with a 95% interval of [−0.91, +2.79], which
spans zero. Two baselines are significantly *worse*: the mid-price rule and the
imbalance threshold both have intervals excluding zero on the wrong side.

The lift is reported as a paired interval, not a point estimate. The two
accuracies come from the same samples, so the difference is paired:
`d_i = correct_i − (y_i == 1)`, and `sd(d)/sqrt(n)` is the standard error.
Treating them as independent proportions would overstate it.

An earlier version of this answer said "logistic regression and ridge score at
or below it", which was false — ridge scores above. The point estimate had been
quoted without an interval, which is how a fraction of a percentage point came
to look like a finding.

---

## G. C++, performance and operations (115–120)

**115. Why is the binding narrow?**
A wide binding is how two implementations drift. `test_the_binding_is_narrow`
lists the entire agreed facade; anything outside it fails the build. Widening it
is a deliberate act that belongs in ADR-002.

**116. Why does the binding take a dict rather than a struct-like object?**
Because the conversion is explicit, field by field. A `**kwargs`-style conversion
would silently accept a renamed field, and the two implementations would diverge
without a test failing.

**117. What are the measured throughputs?**
On the development machine, with the C++ core built: 1.1 M events/s for book
update, 1.0 M for the C++ path through pybind11 (including the dict conversion),
113 K for the full Python replay loop. Every artefact records the machine, the
commit and the interpreter, and carries the disclaimer that this is a local
microbenchmark and not comparable to exchange or network latency.

**118. Why is the full replay loop so much slower than the book update?**
Because the Python feature engine dominates it — `feature_update` alone is
113 K events/s. That is a known, measured bottleneck and the reason the C++ core
covers the book and matching first.

**119. What does CI run?**
Four jobs: Python 3.11/3.12/3.13 (lint, mypy, tests, architecture, leakage, demo
smoke); C++ Release with unit tests **and the differential suite**; C++ with ASan
and UBSan; and a reproducibility job that asserts a seed reproduces an execution
and that the git commit can be determined.

**120. What fails the build?**
A layering violation, an import cycle, a file over 600 lines, a function over 100
lines, a framework import outside `interfaces`, `time.sleep` in the simulation
path, a star import, a catch-all module name, a leaked feature, a shuffled split,
a Python/C++ divergence, or a non-reproducible run. Every one of those is a rule
this project states somewhere; a rule not enforced by a test is a preference, not
an invariant.
