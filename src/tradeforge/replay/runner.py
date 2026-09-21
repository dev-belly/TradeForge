"""Event-driven replay driver.

One pass over the event stream, in this exact order:

    validate -> advance clock -> drain arrivals -> notify queue (pre-event book)
    -> apply to book -> compute features -> hand MarketState to the simulator

The queue is notified *before* the event mutates the book, because the queue
model needs the level size as it was when the trade/cancel arrived. Nothing here
looks ahead: `MarketState` is built only from what has already been applied.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from ..data.validation import EventValidator, ValidationReport
from ..domain.book import MarketState
from ..domain.enums import ValidationMode
from ..domain.events import MarketEvent
from ..domain.exceptions import EventValidationError
from ..domain.protocols import MarketDataAdapter
from ..execution.result import ExecutionResult
from ..microstructure.features import MicrostructureEngine
from .clock import SimulationClock

if TYPE_CHECKING:  # imported for typing only; runtime import would cycle
    from ..execution.simulator import ExecutionSimulator


class ReplayObserver(Protocol):
    """Passive watcher of the replay. Cannot influence the simulation.

    Observers receive every event and the state built from it. They exist so TCA
    can record the market *after* the execution window (markouts are a
    post-trade measurement, which is why forward-looking here is legitimate and
    never fed back into a policy).
    """

    def observe(self, event: MarketEvent, state: MarketState) -> None: ...


@dataclass(frozen=True)
class ReplaySettings:
    snapshot_depth: int = 10
    max_events: int | None = None
    strict_monotonic_clock: bool = True
    # Keep replaying after the parent window closes until this timestamp, so
    # post-trade markouts have data. None = stop as soon as the execution ends.
    stop_after_ns: int | None = None


@dataclass
class ReplayOutcome:
    """Replay artefacts that are not part of a single execution result."""

    n_events: int = 0
    validation: ValidationReport | None = None
    book_violations: list[str] = field(default_factory=list)
    first_timestamp_ns: int | None = None
    last_timestamp_ns: int | None = None


class ReplayRunner:
    """Drives one replay pass, feeding a book, features and one simulator."""

    def __init__(
        self,
        *,
        events: Iterable[MarketEvent],
        book: object,
        features: MicrostructureEngine,
        clock: SimulationClock,
        validator: EventValidator | None = None,
        settings: ReplaySettings | None = None,
        outcome: ReplayOutcome | None = None,
        observers: Sequence[ReplayObserver] = (),
    ) -> None:
        self._events = events
        self._book = book
        self._features = features
        self._clock = clock
        self._validator = validator
        self._observers = tuple(observers)
        self._settings = settings or ReplaySettings()
        # Shareable so a violation hook registered on the book before the runner
        # exists writes into the same object the caller reads afterwards.
        self.outcome = outcome if outcome is not None else ReplayOutcome()

    # ------------------------------------------------------------------ run

    def run(self, simulator: ExecutionSimulator) -> ExecutionResult:
        stream: Iterable[MarketEvent] = self._events
        if self._validator is not None:
            stream = self._validator.validate(stream)

        last_state: MarketState | None = None
        depth = self._settings.snapshot_depth
        for event in stream:
            self._advance(event)
            self._drain()
            if simulator.is_finished and self._past_stop(event.exchange_timestamp_ns):
                # The parent window closed and nothing is in flight: replaying
                # the rest of the session would only burn CPU.
                break
            # Pre-event book: the queue model reads level sizes from it.
            simulator.observe_event(event)
            self._book.apply(event)  # type: ignore[attr-defined]
            snapshot = self._book.snapshot(depth)  # type: ignore[attr-defined]
            self._features.on_event(event, snapshot)
            state = self._features.to_market_state(snapshot)
            simulator.observe_state(state, snapshot)
            self._drain()

            for observer in self._observers:
                observer.observe(event, state)

            last_state = state
            self.outcome.n_events += 1
            if self.outcome.first_timestamp_ns is None:
                self.outcome.first_timestamp_ns = event.exchange_timestamp_ns
            self.outcome.last_timestamp_ns = event.exchange_timestamp_ns
            limit = self._settings.max_events
            if limit is not None and self.outcome.n_events >= limit:
                break

        self._drain()
        if self._validator is not None:
            self.outcome.validation = self._validator.report
        return simulator.finalize(last_state)

    # ------------------------------------------------------------- internals

    def _advance(self, event: MarketEvent) -> None:
        now = self._clock.now_ns
        if event.exchange_timestamp_ns < now:
            message = (
                f"non-monotonic event: seq={event.sequence_id} at "
                f"{event.exchange_timestamp_ns} < clock {now}"
            )
            if self._settings.strict_monotonic_clock:
                raise EventValidationError(message)
            return
        self._clock.advance_to(event.exchange_timestamp_ns)

    def _past_stop(self, timestamp_ns: int) -> bool:
        stop = self._settings.stop_after_ns
        return stop is None or timestamp_ns >= stop

    def _drain(self) -> None:
        """Run every scheduled action due at or before now (order arrivals)."""
        for item in list(self._clock.pop_due()):
            item.action(item.due_ns)

    # -------------------------------------------------------------- helpers

    @staticmethod
    def violation_collector(outcome: ReplayOutcome) -> object:
        """Hook for `MbpBook(on_violation=...)` in WARN mode."""

        def hook(violation: object, message: str, event: MarketEvent) -> None:
            outcome.book_violations.append(f"{getattr(violation, 'value', violation)}: {message}")

        return hook


def replay_mode_from_config(config: dict[str, object]) -> ValidationMode:
    replay = config.get("replay", {}) if isinstance(config, dict) else {}
    if not isinstance(replay, dict):
        raise TypeError("config must contain a 'replay' mapping")
    return ValidationMode(str(replay.get("validation_mode", "STRICT")).upper())


def stream_from_adapter(adapter: MarketDataAdapter) -> Iterable[MarketEvent]:
    """Materialise nothing: adapters must yield lazily."""
    return adapter.events()
