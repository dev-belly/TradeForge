# ADR-004: Event-driven replay, never a materialized DataFrame

* **Status:** Accepted
* **Date:** 2026-09-08

## Context

Loading a full session into a DataFrame and letting a policy index it is the
single easiest way to leak the future (and it is invisible in review).

## Decision

* The event source is a lazy `Iterator[MarketEvent]`. There is no "full day" in
  memory for a strategy to reach into.
* The policy receives an immutable `MarketState` **value object** containing only
  present/past-derived quantities.
* The clock advances monotonically; a policy decision at time `t` cannot observe
  any event with timestamp `> t`, nor a later event with timestamp `== t`.
* Latency is applied by scheduling an arrival at `t + Δ` and letting the market
  continue in between — never `time.sleep`.
* VWAP profiles come from prior sessions or a causal estimator; using the current
  session's full volume curve raises `LeakageError`.

## Consequences

* Slightly more code than vectorized slicing, but leakage becomes structurally
  hard rather than "please remember not to".
* Feature windows must be causal deques, which also makes them streaming-ready.
* Throughput is benchmarked; if the Python driver becomes the bottleneck the C++
  replay loop (ADR-002) takes over.
