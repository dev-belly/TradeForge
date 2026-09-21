# ADR-003: Queue position from L2/MBP is an estimate, never an observation

* **Status:** Accepted
* **Date:** 2026-09-08

## Context

MBP data reports *net* aggregated size per price level. A decrease of 100 shares
is equally consistent with one cancel of 100, 100 cancels of 1, or 60 executed
plus 40 cancelled. The position of removed shares relative to our order is
**not identifiable** from L2.

## Decision

1. `queue_mode` is `EXACT` only when the source provides order identity (MBO/L3).
   Otherwise the system **refuses** to construct `ExactQueueModel`.
2. For MBP we use `ApproximateQueueModel` with explicit update rules:
   * additions go behind us;
   * trade volume consumes from the front;
   * unexplained net decrease is split by `cancel_ahead_policy`
     ∈ {`OPTIMISTIC`, `NEUTRAL`, `CONSERVATIVE`}.
3. Identifiers, docstrings and report columns that come from this model contain
   `estimate` / `approximate`.
4. Queue sensitivity (all three policies) is a **first-class experiment**, and
   the spread between policies is reported as model uncertainty.
5. Price touch alone never fills a passive order.

## Consequences

* Fill rates are ranges, not point estimates. That is the honest answer.
* Any headline result must state the cancel-ahead policy used.
* Users with L3 data get exact FIFO and a different, clearly better capability —
  which is precisely the point of the capability matrix.
