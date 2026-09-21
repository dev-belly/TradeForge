# Data: adapters, capabilities and validation

## The one rule

**No capability may be claimed beyond what the source declares.** A feature that
needs something the data does not contain raises `DataCapabilityError` rather
than degrading into a guess. This is enforced in `domain/capability.py` and
tested in `tests/unit/test_domain.py::TestCapabilityGating`.

The rule exists because the alternative is worse than an error. A queue position
estimated from aggregated levels and reported as exact is a fabricated number,
and a fabricated number in an execution model produces a backtest that is wrong
in a direction nobody can see.

## Capability tiers

| Tier | Contains | Enables |
|---|---|---|
| `L1` | best bid/ask, last trade | spread, mid, microprice, top imbalance, volatility |
| `L2_MBP` | aggregated levels with quantities | depth curve, multi-level imbalance, depth slope, **approximate** queue, OFI |
| `L3_MBO` | per-order adds, cancels and executions | order-level cancels, **exact** FIFO queue, true trade aggressor |

Two capabilities are *tier-exact* rather than "at least": `EXACT_QUEUE`,
`ORDER_LEVEL_CANCEL` and `MBO_RECONSTRUCTION` require L3 specifically. A higher
tier does not grant them implicitly, and the check is deliberately not monotone.

```python
from tradeforge.domain.capability import Capability, describe, require
from tradeforge.domain.enums import DataType

describe(DataType.L2_MBP)["exact_queue"]     # False
require(DataType.L2_MBP, Capability.EXACT_QUEUE, context="queue model")
# DataCapabilityError: exact_queue requires at least L3_MBO data, active source
# declares L2_MBP (queue model). This is a hard stop: TradeForge does not
# fabricate market detail.
```

## Adapters

```bash
python -m tradeforge.interfaces.cli data adapters
python -m tradeforge.interfaces.cli data inspect --adapter synthetic --limit 5000
```

| Adapter | Declares | Notes |
|---|---|---|
| `synthetic` | `L2_MBP` | Deterministic. Maintains a shadow book so it only ever emits valid events. |
| `synthetic-mbo` | `L3_MBO` | Same generator with order identity, so the exact queue path is testable end to end. |
| `lobster` | `L3_MBO` | Message-file reconstruction: ADD, CANCEL, EXECUTE, DELETE, REPLACE. |
| `binance` | `L2_MBP` | Polled depth snapshots. Each poll emits `CLEAR` + `SNAPSHOT`. |
| `normalized` | as declared in the file | Reads back what an earlier run wrote. |

### The `CLEAR` consequence

A polled L2 feed cannot track a queue across a snapshot: the displayed level
contents are re-stated, not updated, so our position within the level becomes
unknowable. The simulator therefore **pulls resting passive orders on `CLEAR`**
and counts them, rather than pretending the queue survived. On a stream that
clears every second, that makes passive execution nearly useless — which is the
truthful answer about polled data, not a limitation to be papered over.

LOBSTER-style event streams never emit `CLEAR`, so the path never triggers there.

## The normalized event

Every adapter produces the same record. Nothing downstream sees a
source-specific shape.

```python
MarketEvent(
    sequence_id=...,            # source-local, monotonic
    exchange_timestamp_ns=...,  # venue time
    symbol="SYNTH",
    event_type=EventType.TRADE,
    side=Side.BUY,
    price_ticks=9_999,          # int64 ticks, never a float
    quantity_base=250,          # base units, never a float
    order_id=None,              # present only for L3
    trade_id=None,
    flags=EventFlag.AGGRESSOR_BUY,
    source="synthetic",
    receive_timestamp_ns=None,  # never synthesized
)
```

Ordering is `(exchange_timestamp_ns, sequence_id)`, so same-timestamp events keep
their source order and a replay is deterministic across machines.

`receive_timestamp_ns` is `None` when the source does not provide it. It is
**never** synthesized, because a fabricated receive timestamp would make the
`observed` latency model report a measurement that was actually a scenario.

## The validator

```python
from tradeforge.data.validation import EventValidator, ValidationSettings
from tradeforge.domain.enums import ValidationMode

validator = EventValidator(ValidationSettings(mode=ValidationMode.STRICT))
for event in validator.validate(adapter.events()):
    ...
print(validator.report.to_dict())
```

| Mode | Behaviour on a problem |
|---|---|
| `STRICT` | raise — the default |
| `WARN` | record and pass through |
| `REPAIR` | fix where it is unambiguous, flag with `EventFlag.REPAIRED`, record |

Checks: timestamp ordering, sequence monotonicity, required side/price/quantity
per event type, non-positive quantity, non-positive price, symbol consistency.

`REPAIR` only ever fills in a value the event type *requires* and the source
omitted, and it marks the event. It never invents a price or a side.

## Book reconstruction choices

Documented because each one is a decision, not an obvious default:

| Event | MBP book | MBO book |
|---|---|---|
| `ADD` | `+= quantity` at the price | append to the level queue |
| `CANCEL` | `-= quantity`; raises if the level is short | remove from the named order; capped by its remaining size |
| `TRADE` | consume the **resting** side at the print price | consume from the front of the resting queue |
| `SNAPSHOT` | absolute set for one level | refused — rebuild from order events |
| `MODIFY` | **refused** — no order identity | absolute size change; priority per config |
| `CLEAR` | empty both sides | empty both sides |

A single print cannot reveal a multi-level sweep, so a trade consumes depth only
at its own price. Inferring a sweep would be a guess about depth we did not see.

## Invariants

| Condition | Default | Why |
|---|---|---|
| Crossed (`bid > ask`) | `STRICT` | always a reconstruction failure |
| Locked (`bid == ask`) | `WARN` | genuinely happens on real venues; tolerated and reported, never silently "fixed" |
| Negative depth | `STRICT` | a cancel larger than the level means the stream is inconsistent |
| Unknown order id | `STRICT` | L3 only |
| Trade beyond depth | `STRICT` | the book and the tape disagree |

A locked market is reported through the `on_violation` hook rather than deleted,
because deleting a level would change the reconstructed book to hide a fact about
the source.
