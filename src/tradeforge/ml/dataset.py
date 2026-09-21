"""Fill-probability dataset construction.

The target is `fill_within_horizon`: if we post a passive order at the current
touch right now, does the queue ahead of us get consumed within `horizon_ns`?

Two decisions define what the label means, and both are configurable because
neither is knowable from L2 data:

  * **Cancels ahead of us.** From aggregated levels we cannot tell whether a
    shrinking level lost shares in front of or behind our hypothetical order.
    `count_cancels_as_ahead=False` (the default) means only *trades* consume the
    queue, i.e. the CONSERVATIVE reading. Setting it True assumes every cancel
    was ahead of us, which raises the label rate and is stated in the report.
  * **Queue position at entry.** We join at the back of the displayed level, the
    same assumption the approximate queue model makes. Consistent assumptions
    between the simulator and the ML label are what make the two comparable.

Causality: features come from `MicrostructureEngine`, which only ever sees
events up to and including the current one. Labels look forward - that is what a
label is - and the split logic in `split.py` is responsible for making sure a
label's forward window never crosses into the next split.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..domain.book import BookSnapshot
from ..domain.enums import DataType, EventType, Side
from ..domain.events import MarketEvent
from ..matching.engine import resting_price_ticks
from ..microstructure.features import FeatureSet, MicrostructureEngine
from ..orderbook import BookSettings, create_book


@dataclass(frozen=True, slots=True)
class SampleConfig:
    """How densely to sample and how far forward the label reaches."""

    sample_interval_ns: int = 5_000_000_000
    horizon_ns: int = 30_000_000_000
    side: Side = Side.BUY
    count_cancels_as_ahead: bool = False
    snapshot_depth: int = 10

    def __post_init__(self) -> None:
        if self.sample_interval_ns <= 0:
            raise ValueError("sample_interval_ns must be positive")
        if self.horizon_ns <= 0:
            raise ValueError("horizon_ns must be positive")


@dataclass(frozen=True, slots=True)
class Sample:
    """One labelled observation."""

    timestamp_ns: int
    features: FeatureSet
    price_ticks: int
    queue_ahead_base: int
    label: int
    resolved_ns: int


@dataclass
class _Pending:
    """A sample awaiting its label.

    `index` is the position this sample will occupy in the output. Samples are
    resolved in whatever order the market resolves them, which is NOT their
    creation order - a sample issued later can fill sooner. Reserving the slot
    at creation time keeps the output time ordered, which the purged split
    depends on.
    """

    index: int
    timestamp_ns: int
    features: FeatureSet
    price_ticks: int
    queue_ahead_base: int
    deadline_ns: int


@dataclass
class DatasetReport:
    """What the builder saw. Printed next to any model trained on the output."""

    n_events: int = 0
    n_samples: int = 0
    n_positive: int = 0
    n_expired: int = 0
    n_level_disappeared: int = 0
    sample_interval_ns: int = 0
    horizon_ns: int = 0
    side: str = "BUY"
    count_cancels_as_ahead: bool = False

    @property
    def base_rate(self) -> float:
        if self.n_samples == 0:
            return 0.0
        return self.n_positive / self.n_samples

    def to_dict(self) -> dict[str, object]:
        return {
            "n_events": self.n_events,
            "n_samples": self.n_samples,
            "n_positive": self.n_positive,
            "base_rate": self.base_rate,
            "n_expired": self.n_expired,
            "n_level_disappeared": self.n_level_disappeared,
            "sample_interval_ns": self.sample_interval_ns,
            "horizon_ns": self.horizon_ns,
            "side": self.side,
            "count_cancels_as_ahead": self.count_cancels_as_ahead,
            "label_caveat": (
                "Cancels are assumed to sit BEHIND our hypothetical order; only "
                "trades consume the queue ahead. L2 data cannot distinguish the two."
            ),
        }


class FillDatasetBuilder:
    """Single-pass builder. Events are consumed lazily, one at a time."""

    def __init__(
        self,
        *,
        symbol: str,
        config: SampleConfig,
        data_type: DataType = DataType.L2_MBP,
        settings: BookSettings | None = None,
        feature_engine: MicrostructureEngine | None = None,
    ) -> None:
        self._symbol = symbol
        self._config = config
        self._book = create_book(
            symbol=symbol,
            settings=settings or BookSettings.from_dict({}),
            data_type=data_type,
        )
        self._features = feature_engine or MicrostructureEngine()
        self._pending: list[_Pending] = []
        # Slots reserved at creation time so the output stays time ordered.
        self._slots: list[Sample | None] = []
        self._next_sample_ns: int | None = None
        self.report = DatasetReport(
            sample_interval_ns=config.sample_interval_ns,
            horizon_ns=config.horizon_ns,
            side=config.side.value,
            count_cancels_as_ahead=config.count_cancels_as_ahead,
        )

    @property
    def samples(self) -> tuple[Sample, ...]:
        """Labelled samples in creation (time) order."""
        return tuple(s for s in self._slots if s is not None)

    def run(self, events: Iterable[MarketEvent]) -> tuple[Sample, ...]:
        for event in events:
            self._step(event)
        # Anything still pending at the end of the stream never reached its
        # deadline. Dropping it is the only honest option: labelling it 0 would
        # invent a negative that the data did not observe.
        self.report.n_expired += len(self._pending)
        self._pending.clear()
        # Slots left empty correspond to dropped samples: compact them out so
        # the returned sequence has no holes.
        self._slots = [s for s in self._slots if s is not None]
        self.report.n_samples = len(self._slots)
        return self.samples

    # ------------------------------------------------------------- internals

    def _step(self, event: MarketEvent) -> None:
        self.report.n_events += 1
        ts = event.exchange_timestamp_ns
        self._book.apply(event)
        snapshot = self._book.snapshot(self._config.snapshot_depth)
        self._features.on_event(event, snapshot)

        self._resolve_pending(event, ts)
        self._maybe_emit(ts, snapshot)

    def _resolve_pending(self, event: MarketEvent, ts: int) -> None:
        if not self._pending:
            return
        still_pending: list[_Pending] = []
        for pending in self._pending:
            if self._consumes_queue(event, pending):
                pending.queue_ahead_base -= min(pending.queue_ahead_base, event.quantity_base or 0)
                if pending.queue_ahead_base <= 0:
                    self._emit_sample(pending, label=1, resolved_ns=ts)
                    continue
            if self._level_gone(event, pending):
                self.report.n_level_disappeared += 1
                self._emit_sample(pending, label=0, resolved_ns=ts)
                continue
            if ts >= pending.deadline_ns:
                self.report.n_expired += 1
                self._emit_sample(pending, label=0, resolved_ns=ts)
                continue
            still_pending.append(pending)
        self._pending = still_pending

    def _consumes_queue(self, event: MarketEvent, pending: _Pending) -> bool:
        if event.event_type is not EventType.TRADE:
            return self._config.count_cancels_as_ahead and event.event_type is EventType.CANCEL
        if event.price_ticks != pending.price_ticks:
            return False
        aggressor = event.aggressor_side() or event.side
        # A trade against our resting side consumes our queue.
        return aggressor is not None and aggressor.opposite is self._config.side

    def _level_gone(self, event: MarketEvent, pending: _Pending) -> bool:
        if event.event_type is EventType.CLEAR:
            return True
        if event.event_type is not EventType.CANCEL or event.side is not self._config.side:
            return False
        if event.price_ticks != pending.price_ticks:
            return False
        remaining = self._book.level_size_base(self._config.side, pending.price_ticks)
        return remaining <= 0

    def _maybe_emit(self, ts: int, snapshot: BookSnapshot) -> None:
        if self._next_sample_ns is None:
            self._next_sample_ns = ts
        if ts < self._next_sample_ns:
            return
        self._next_sample_ns = ts + self._config.sample_interval_ns

        # A PASSIVE order joins its own side of the book, not the touch it would
        # cross into. Using `touch_ticks` here would post a BUY on the ask and
        # then look for queue ahead on the wrong side of the book.
        price = resting_price_ticks(snapshot, self._config.side, 0)
        if price is None:
            return
        queue_ahead = self._book.level_size_base(self._config.side, price)
        if queue_ahead <= 0:
            # Nothing ahead of us means we would fill on the next trade. That is
            # a real state, but it is not a queue-position question, so it is
            # excluded rather than labelled.
            return
        index = len(self._slots)
        self._slots.append(None)
        self._pending.append(
            _Pending(
                index=index,
                timestamp_ns=ts,
                features=self._features.observe(snapshot),
                price_ticks=price,
                queue_ahead_base=queue_ahead,
                deadline_ns=ts + self._config.horizon_ns,
            )
        )

    def _emit_sample(self, pending: _Pending, *, label: int, resolved_ns: int) -> None:
        self._slots[pending.index] = Sample(
            timestamp_ns=pending.timestamp_ns,
            features=pending.features,
            price_ticks=pending.price_ticks,
            queue_ahead_base=pending.queue_ahead_base,
            label=label,
            resolved_ns=resolved_ns,
        )
        self.report.n_samples += 1
        self.report.n_positive += label


def feature_matrix(samples: Iterable[Sample]) -> tuple[list[list[float]], list[int]]:
    """Numeric feature matrix + labels, in the order `FeatureSet.numeric_fields`."""
    names = FeatureSet.numeric_fields()
    rows: list[list[float]] = []
    labels: list[int] = []
    for sample in samples:
        payload = sample.features.as_dict()
        rows.append([_as_float(payload.get(name)) for name in names])
        labels.append(sample.label)
    return rows, labels


def _as_float(value: object) -> float:
    """`None` becomes NaN, which the preprocessing step imputes explicitly.

    Imputing to zero here would silently mean "spread of 0 ticks" or "mid of
    0", both of which are lies about the market.
    """
    if value is None:
        return float("nan")
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    return float("nan")
