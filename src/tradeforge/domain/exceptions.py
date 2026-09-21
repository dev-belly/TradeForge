"""Domain exceptions.

Distinct error types matter: a capability violation and a data-integrity
violation and a future-leakage violation are three different bugs and must not
collapse into one `ValueError`.
"""

from __future__ import annotations


class TradeForgeError(Exception):
    """Base class for every TradeForge error."""


class DataCapabilityError(TradeForgeError):
    """Raised when a feature requires data the active source cannot provide.

    Example: constructing an exact FIFO queue model from L2/MBP data.
    """


class LeakageError(TradeForgeError):
    """Raised when a computation would use information from the future."""


class EventValidationError(TradeForgeError):
    """Raised by the validator in STRICT mode."""

    def __init__(self, message: str, *, sequence_id: int | None = None) -> None:
        super().__init__(message)
        self.sequence_id = sequence_id


class BookIntegrityError(TradeForgeError):
    """Book invariant violated (negative depth, crossed book, unknown order)."""


class OrderStateError(TradeForgeError):
    """Illegal OMS state transition."""


class InstrumentError(TradeForgeError):
    """Price/quantity violates the instrument specification."""


class ConfigurationError(TradeForgeError):
    """Config file missing keys or holds an illegal combination."""


class AdapterError(TradeForgeError):
    """A market-data adapter could not read or interpret its source."""


class MatchingError(TradeForgeError):
    """The matching engine received an order it cannot legally process."""


class GuardrailBreachError(TradeForgeError):
    """A simulation guardrail (max participation, notional, ...) was hit."""
