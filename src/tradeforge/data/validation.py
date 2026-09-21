"""Event stream validation.

Runs before the book ever sees an event. Modes:
  STRICT - raise on the first violation
  WARN   - pass the event through, record the issue
  REPAIR - drop the offending event, record the issue and the repair

Repairs are always recorded: a silently "fixed" dataset is worse than a broken
one because nobody notices.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from ..domain.enums import EventFlag, EventType, ValidationMode
from ..domain.events import MarketEvent
from ..domain.exceptions import EventValidationError

_PRICE_QTY_EVENTS = frozenset(
    {EventType.ADD, EventType.CANCEL, EventType.MODIFY, EventType.REPLACE, EventType.TRADE}
)
_SIDE_REQUIRED = frozenset(
    {EventType.ADD, EventType.CANCEL, EventType.MODIFY, EventType.REPLACE, EventType.SNAPSHOT}
)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    message: str
    sequence_id: int | None
    action: str  # raised | warned | dropped


@dataclass(slots=True)
class ValidationReport:
    n_input: int = 0
    n_output: int = 0
    issues: list[ValidationIssue] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)

    def record(self, issue: ValidationIssue) -> None:
        self.issues.append(issue)
        self.counters[issue.code] = self.counters.get(issue.code, 0) + 1

    @property
    def n_issues(self) -> int:
        return len(self.issues)

    @property
    def dropped(self) -> int:
        return self.n_input - self.n_output

    def to_dict(self) -> dict[str, object]:
        return {
            "n_input": self.n_input,
            "n_output": self.n_output,
            "n_issues": self.n_issues,
            "dropped": self.dropped,
            "counters": dict(self.counters),
            "issues": [
                {
                    "code": i.code,
                    "message": i.message,
                    "sequence_id": i.sequence_id,
                    "action": i.action,
                }
                for i in self.issues[:100]
            ],
        }


@dataclass(frozen=True)
class ValidationSettings:
    mode: ValidationMode = ValidationMode.STRICT
    max_recorded_issues: int = 1000

    @classmethod
    def from_dict(cls, payload: dict[str, object] | None) -> ValidationSettings:
        if not payload:
            return cls()
        return cls(mode=ValidationMode(str(payload.get("mode", "STRICT")).upper()))


class EventValidator:
    """Streaming validator. Stateful by necessity (ordering checks)."""

    def __init__(self, settings: ValidationSettings | None = None) -> None:
        self._settings = settings or ValidationSettings()
        self._report = ValidationReport()
        self._last_timestamp_ns: int | None = None
        self._last_sequence_id: int | None = None
        self._symbol: str | None = None

    @property
    def report(self) -> ValidationReport:
        return self._report

    def validate(self, events: Iterable[MarketEvent]) -> Iterator[MarketEvent]:
        for event in events:
            self._report.n_input += 1
            problem = self._check(event)
            if problem is None:
                self._accept(event)
                self._report.n_output += 1
                yield event
                continue
            code, message = problem
            action = self._handle(code, message, event)
            if action == "dropped":
                continue
            self._accept(event)
            self._report.n_output += 1
            yield event

    # ------------------------------------------------------------- checking

    def _check(self, event: MarketEvent) -> tuple[str, str] | None:
        ts = event.exchange_timestamp_ns
        if self._last_timestamp_ns is not None and ts < self._last_timestamp_ns:
            return (
                "out_of_order_timestamp",
                f"timestamp {ts} precedes previous {self._last_timestamp_ns}",
            )
        if self._last_sequence_id is not None and event.sequence_id <= self._last_sequence_id:
            code = (
                "duplicate_sequence_id"
                if event.sequence_id == self._last_sequence_id
                else "sequence_not_monotonic"
            )
            return (code, f"sequence {event.sequence_id} after {self._last_sequence_id}")
        if self._symbol is not None and event.symbol != self._symbol:
            return ("symbol_mismatch", f"{event.symbol} != {self._symbol}")
        if event.event_type in _PRICE_QTY_EVENTS:
            if event.price_ticks is None or event.price_ticks <= 0:
                return ("invalid_price", f"price_ticks={event.price_ticks}")
            if event.quantity_base is None or event.quantity_base <= 0:
                return ("invalid_quantity", f"quantity_base={event.quantity_base}")
        if event.event_type in _SIDE_REQUIRED and event.side is None:
            return ("missing_side", f"{event.event_type.value} without side")
        if event.flags & EventFlag.AGGRESSOR_BUY and event.flags & EventFlag.AGGRESSOR_SELL:
            return ("conflicting_aggressor_flags", "both AGGRESSOR_BUY and AGGRESSOR_SELL set")
        return None

    # -------------------------------------------------------------- actions

    def _handle(self, code: str, message: str, event: MarketEvent) -> str:
        text = f"{message} (seq={event.sequence_id})"
        if self._settings.mode is ValidationMode.STRICT:
            self._report.record(ValidationIssue(code, message, event.sequence_id, "raised"))
            raise EventValidationError(text, sequence_id=event.sequence_id)
        if self._settings.mode is ValidationMode.REPAIR:
            if len(self._report.issues) < self._settings.max_recorded_issues:
                self._report.record(ValidationIssue(code, message, event.sequence_id, "dropped"))
            return "dropped"
        if len(self._report.issues) < self._settings.max_recorded_issues:
            self._report.record(ValidationIssue(code, message, event.sequence_id, "warned"))
        return "warned"

    def _accept(self, event: MarketEvent) -> None:
        self._last_timestamp_ns = event.exchange_timestamp_ns
        self._last_sequence_id = event.sequence_id
        self._symbol = event.symbol
