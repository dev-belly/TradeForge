# Data Capability Matrix

> Rule: **no feature may run on data that cannot support it.**
> `tradeforge.domain.capability.require()` raises `DataCapabilityError` instead of
> silently degrading. Naming convention: anything derived from MBP/L2 queue
> reasoning must contain `estimate` / `approximate`.

## 1. Capability → required data

| Capability | Minimum data | Notes |
|---|---|---|
| Best bid / ask | L1 | |
| Quoted spread, relative spread | L1 | |
| Mid price | L1 | |
| Microprice | L1 (top size) | |
| Top-level imbalance | L1 | |
| Depth curve (multi-level) | L2 (MBP) | |
| Multi-level imbalance | L2 (MBP) | |
| Depth slope | L2 (MBP) | |
| MBP book reconstruction | L2 (MBP) | Full-diffs or snapshots; see §3 |
| MBO book reconstruction | L3 (MBO) | Order identity required |
| **Exact FIFO queue position** | **L3 (MBO)** | **Impossible from L2** |
| **Approximate queue position** | L2 (MBP) | Estimate only |
| Order-level cancel attribution | L3 (MBO) | |
| Cancellation intensity | L2 (MBP, with caveats) | L2 shows *net* level change, not cancels |
| Order flow imbalance (event-based) | L2 (MBP) or L3 | Definition depends on tier |
| Trade aggressor side | Dataset dependent | Not inferable from trades alone |
| Realized volatility | Trades or quotes | |
| Market VWAP benchmark | Trades | |
| Cancel-ahead vs behind resolution | L3 (MBO) | L2 must use a policy |

## 2. Adapter capability declaration

| Adapter | Tier | Provides | Status |
|---|---|---|---|
| `normalized` (canonical parquet/csv) | declared per dataset | everything in the schema | VERIFIED |
| `synthetic` | L2 (MBP) + trades, or L3 (MBO) on request | deterministic sample | VERIFIED |
| `lobster` | L3 (MBO) | order-level add/cancel/modify/execute | IMPLEMENTED, NOT VERIFIED against real LOBSTER files (no redistribution licence) |
| `binance` | L2 (MBP) snapshots + trades | real public market data | IMPLEMENTED, network dependent |

`market_data_type` is recorded in every run manifest and printed in every report.

## 3. What L2 does **not** tell you

An MBP level update only reports the **net** change of aggregated displayed size.

| Observed | Possible truths |
|---|---|
| Level size −100, no trade | One cancel of 100; 100 cancels of 1; any mix |
| Level size −100, trade of 100 | 100 executed from the queue front |
| Level size −100, trade of 60 | 60 executed + 40 cancelled — **position of the 40 unknown** |
| Level size +100 | New order(s) at the **back** of the level (position known: behind) |
| Level size unchanged, trade of 50 | 50 executed + 50 added at the back |

Consequences implemented in `ApproximateQueueModel`:

1. Additions always go **behind** us (safe).
2. Trade volume consumes from the **front** (safe).
3. Net size decrease not explained by trades is **unattributable** →
   `cancel_ahead_policy` ∈ {`OPTIMISTIC`, `NEUTRAL`, `CONSERVATIVE`}.
4. The spread between policies is reported as queue-model uncertainty.

## 4. Queue claim policy

| Claim | Allowed? |
|---|---|
| "exact queue position" for MBO/L3 sources | Yes, with `queue_mode=EXACT` |
| "exact queue position" for MBP/L2 sources | **Never** |
| "estimated queue ahead" for MBP/L2 | Yes, always labelled |
| "fill probability" | Only as a model output with the queue mode stated |
| "L3 reconstruction from trades+quotes" | **Never** |

## 5. Timestamp capability

| Source | exchange ts | receive ts | Notes |
|---|---|---|---|
| LOBSTER | seconds (float in file → ns int64) | none | sub-second ordering from message sequence only |
| Binance REST snapshot | ms | none (HTTP fetch time recorded separately, labelled) | |
| normalized parquet | declared per file | optional column | absent ⇒ stays `None` |

`receive_timestamp_ns` is **never** synthesized. If absent, latency is applied as
a declared scenario offset, and the run manifest records
`latency_basis = "scenario"`.
