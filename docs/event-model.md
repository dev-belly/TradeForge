# Event Model

## 1. Normalized event schema

All sources are converted to `MarketEvent` (frozen dataclass) before entering the
domain.

| Field | Type | Meaning |
|---|---|---|
| `sequence_id` | `int64` | Source-local monotonic id |
| `exchange_timestamp_ns` | `int64` | Venue event time; never synthesized |
| `symbol` | `str` | Normalized symbol |
| `event_type` | `EventType` | see §2 |
| `side` | `Side \| None` | BUY / SELL; `None` only for SNAPSHOT / HALT / RESUME / CLEAR |
| `price_ticks` | `int64 \| None` | integer ticks |
| `quantity_base` | `int64 \| None` | base-asset units |
| `order_id` | `int64 \| None` | MBO/L3 only |
| `trade_id` | `int64 \| None` | TRADE only |
| `flags` | `int` | bitfield, see §3 |
| `source` | `str` | adapter provenance |

`MarketEvent` is **immutable** and hashable. There is no `dict[str, Any]` anywhere
in the event path.

## 2. Event types

| Type | Book effect | Required fields |
|---|---|---|
| `ADD` | insert order at level | side, price, qty, (order_id in MBO) |
| `CANCEL` | remove quantity | side, price, qty, (order_id in MBO) |
| `MODIFY` | change quantity in place (queue preserved if price unchanged in some venues — configurable, default: **queue lost**) | side, price, qty, order_id |
| `REPLACE` | cancel + add (queue lost) | side, price, qty, order_id |
| `TRADE` | consume liquidity at level(s) | side (aggressor if known), price, qty, trade_id |
| `CLEAR` | remove all levels | — |
| `SNAPSHOT` | replace top-N levels | side, price, qty (repeated) |
| `HALT` / `RESUME` | no book change; recorded | — |

Events a source cannot express are **not** fabricated. If LOBSTER has no
`MODIFY`, we emit `REPLACE`-equivalent semantics explicitly.

### Default modify policy
Real venues differ (NASDAQ keeps priority on size-decreasing modify in some
cases). Default: **any quantity decrease loses queue beyond the remaining size**,
and a price change always loses queue. Configurable via
`configs/book.yaml::modify_priority_policy`.

## 3. Flags bitfield

| Bit | Name | Meaning |
|---|---|---|
| 0 | `AGGRESSOR_BUY` | trade aggressor was the buyer |
| 1 | `AGGRESSOR_SELL` | trade aggressor was the seller |
| 2 | `AUCTION` | event belongs to an auction period |
| 3 | `INTERMARKET_SWEEP` | ISO / sweep order |
| 4 | `HIDDEN` | hidden liquidity involved |
| 5 | `REPAIRED` | event was modified by a REPAIR validator |

Aggressor side is **only** set when the source provides it. `MarketState` exposes
`aggressor_side_known` so downstream code never guesses silently.

## 4. Ordering

Ordering key = `(exchange_timestamp_ns, sequence_id)`.

* Events are **not** re-sorted by price or side.
* Equal timestamps keep source sequence order; a policy acting after receiving
  event *k* has not seen event *k+1* even at the same timestamp.
* A strictly decreasing timestamp triggers the out-of-order validator.

## 5. Clock semantics

| Timestamp | Source | Use |
|---|---|---|
| `exchange_timestamp_ns` | data | drives the replay clock |
| `receive_timestamp_ns` | data (optional) | feed-handler latency observation |
| `simulation_timestamp_ns` | simulator | when we processed it |

If `receive_timestamp_ns` is absent it stays `None` — we never invent it.
