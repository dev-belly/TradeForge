# Extending TradeForge

Three extension points are supported, and each has a rule about what it may not
do. The rules are enforced by `tests/architecture/test_layers.py`, which walks the
AST of every module, so a violation fails the build rather than a review.

## Add a market data adapter

An adapter is the **only** place source-specific decoding may live.

```python
# src/tradeforge/data/adapters/mysource.py
from ..domain.enums import DataType, EventType, Side
from ..domain.events import MarketEvent
from .base import BaseAdapter


class MySourceAdapter(BaseAdapter):
    @property
    def name(self) -> str:
        return "mysource"

    @property
    def data_type(self) -> DataType:
        # Declare what the source ACTUALLY provides. Never optimistic: the tier
        # gates every downstream capability, and an inflated tier produces
        # fabricated detail rather than an error.
        return DataType.L2_MBP

    def events(self):
        for record in self._open_source():
            yield MarketEvent(
                sequence_id=record.seq,
                exchange_timestamp_ns=record.ts_ns,
                symbol=record.symbol,
                event_type=_EVENT_TYPES[record.kind],
                side=_SIDES.get(record.side),
                price_ticks=record.price_ticks,
                quantity_base=record.quantity,
                source=self.name,
                receive_timestamp_ns=record.receive_ns or None,  # never synthesize
            )
```

Rules:

* **Yield lazily.** `events()` is an iterator; nothing downstream may need the
  whole day in memory, and materializing it would make the leakage tests weaker.
* **Never synthesize a timestamp.** If the source does not provide a receive
  time, leave it `None`. A fabricated one makes the `observed` latency model
  report a scenario as a measurement.
* **Never invent order identity.** Emit `order_id` only when the source has it.
* **Declare the tier honestly.** Over-declaring is the one bug this architecture
  cannot catch for you.

Register it in `data/registry.py` (one line) and add it to the table in
`docs/guides/data.md`. That is the whole integration.

## Add an execution policy

```python
# src/tradeforge/execution/policies/mypolicy.py
from ...domain.book import MarketState
from ...domain.orders import ChildOrder
from .base import ExecutionPolicyBase


class MyPolicy(ExecutionPolicyBase):
    def __init__(self, *, my_parameter: float = 0.5, **kwargs: object) -> None:
        super().__init__(**kwargs)
        if not 0.0 <= my_parameter <= 1.0:
            raise ValueError("my_parameter must be in [0, 1]")
        self._my_parameter = my_parameter

    @property
    def name(self) -> str:
        return "my_policy"

    def on_market_event(self, state: MarketState) -> list[ChildOrder]:
        due = self.parent_remaining_base // 2
        order = self._emit(state=state, quantity_base=due, slice_index=0)
        return [order] if order else []

    def on_window_end(self, state: MarketState) -> list[ChildOrder]:
        # Called exactly once, after working orders were pulled.
        if self._end_of_window != "sweep_marketable" or self.unfilled_base <= 0:
            return []
        order = self._aggressive(
            state=state, quantity_base=self.unfilled_base, slice_index=1
        )
        return [order] if order else []
```

Rules:

* **The policy sees only `MarketState`.** It has no book, no simulator, no clock
  and no event stream. That is structural, not a convention: "strategy touches
  simulator internals" is impossible rather than forbidden.
* **Validate in `__init__`.** A configuration error must fail at construction,
  not at the first slice.
* **Emit quantity, not price.** The simulator decides where the order goes based
  on the style and the arrival-time book. A policy that sets its own price would
  be able to place liquidity where it never existed.
* **Implement `on_window_end`** if the strategy needs a sweep; the base class
  default does nothing, which is the correct behaviour for `cancel` and `leave`.

Register it in `execution/policies/factory.py` and in
`configs/execution.yaml`. Add it to the grid in
`research/experiments.py` if it belongs in the comparison.

## Add a TCA metric

```python
# src/tradeforge/tca/metrics.py
def compute_cost_metrics(result, benchmarks, spec) -> CostMetrics:
    ...
    my_metric_bps = _my_metric(result, benchmarks),
```

Rules:

* **Positive is worse.** Sign through `side.sign` so the metric can be averaged
  and bootstrapped without anyone flipping a sign by hand.
* **Return `None`, not zero,** when the metric is not measurable. A missing
  benchmark and a zero cost are different facts, and conflating them flatters
  whichever strategy happened to have thinner data.
* **Report coverage next to the value.** `n_mid_observations` and
  `n_trade_prints` are in `CostMetrics` for this reason: a benchmark resting on
  twelve prints must not look like one resting on forty-six thousand.
* **Label approximations as approximations.** If the metric is a residual or a
  proxy, say so in the field name (`residual_impact_bps`) and in every report.

If the metric is a decomposition, assert additivity in
`tests/unit/test_tca.py::TestMetricsAndAttribution`.

## Change the storage schema

Add the table to `storage/schema.py`, a writer method to `storage/writer.py`, and
— if it answers a question worth asking — a numbered query to `sql/`.

Rules:

* **Column names match the dataclass field names** they come from, so a rename in
  the domain surfaces as an obvious schema mismatch rather than a silently wrong
  query.
* **Every table carries `config_fingerprint`**, so a row can always be traced
  back to the configuration that produced it.
* **Write an empty table as schema-only, never skip it.** "No rows" and "the run
  never happened" are different facts and a downstream query must be able to tell
  them apart.

## Change the C++ core

Any change to `cpp/` requires the same change in the Python reference, or the
differential suite fails. That is the point of having both.

```bash
make build-cpp
make test-differential
```

Rules from ADR-002:

* **Prices are `int64` ticks.** There is no `double` price in the core, and
  `HAS_FLOAT_PRICE_COMPARISON` is asserted to be `False` by the parity test.
* **The binding stays narrow.** `TestBindingDiscipline::test_the_binding_is_narrow`
  lists the entire agreed facade. Widening it is a deliberate act that belongs in
  ADR-002, not an accident.
* **Exceptions map one-to-one.** `IntegrityError` in C++ becomes
  `BookIntegrityError` in Python, so a caller cannot tell which backend produced
  an error — only which backend produced the result.

## Adding a test

| Directory | For |
|---|---|
| `tests/unit/` | one component, hand-picked cases |
| `tests/integration/` | the real stack, no mocks |
| `tests/property/` | invariants over generated inputs (hypothesis) |
| `tests/leakage/` | causality: no policy or feature may see the future |
| `tests/architecture/` | layering, cycles, file and function size, banned patterns |
| `tests/differential/` | Python against C++ |

A test that asserts a *number* is less valuable than one that asserts a
*property*. `assert result.filled_base == 19140` breaks on any config change;
`assert maker_fill.queue_wait_ns >= 0` states something that must be true
forever.
