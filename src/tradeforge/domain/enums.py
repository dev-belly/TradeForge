"""Core domain enumerations.

Everything that used to be a magic string (`side="buy"`, `status="filled"`)
lives here. Core logic never compares raw strings.
"""

from __future__ import annotations

from enum import Enum, IntFlag


class Side(Enum):
    """Order / book side."""

    BUY = "BUY"
    SELL = "SELL"

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY

    @property
    def sign(self) -> int:
        """+1 for buy, -1 for sell. Used for all signed cost conventions."""
        return 1 if self is Side.BUY else -1

    def __str__(self) -> str:
        return self.value


class OrderType(Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class TimeInForce(Enum):
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"


class LiquidityFlag(Enum):
    MAKER = "MAKER"
    TAKER = "TAKER"
    UNKNOWN = "UNKNOWN"


class EventType(Enum):
    """Normalized market event types.

    Only emit what the source actually expresses; never fabricate.
    """

    ADD = "ADD"
    CANCEL = "CANCEL"
    MODIFY = "MODIFY"
    REPLACE = "REPLACE"
    TRADE = "TRADE"
    CLEAR = "CLEAR"
    SNAPSHOT = "SNAPSHOT"
    HALT = "HALT"
    RESUME = "RESUME"

    @property
    def mutates_book(self) -> bool:
        return self in _BOOK_MUTATING


_BOOK_MUTATING = frozenset(
    {
        EventType.ADD,
        EventType.CANCEL,
        EventType.MODIFY,
        EventType.REPLACE,
        EventType.TRADE,
        EventType.CLEAR,
        EventType.SNAPSHOT,
    }
)


class OrderStatus(Enum):
    """Research-grade OMS states."""

    NEW = "NEW"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_ORDER_STATES


_TERMINAL_ORDER_STATES = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
)


class ExecutionStatus(Enum):
    """Parent-order lifecycle."""

    PENDING = "PENDING"
    WORKING = "WORKING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class DataType(Enum):
    """Declared market-data capability tier."""

    L1 = "L1"
    L2_MBP = "L2_MBP"
    L3_MBO = "L3_MBO"


class BookMode(Enum):
    MBP = "MBP"
    MBO = "MBO"


class QueueMode(Enum):
    EXACT = "EXACT"
    APPROXIMATE = "APPROXIMATE"


class CancelAheadPolicy(Enum):
    """How unexplained L2 level shrinkage is attributed relative to our order."""

    OPTIMISTIC = "optimistic"
    NEUTRAL = "neutral"
    CONSERVATIVE = "conservative"


class ValidationMode(Enum):
    STRICT = "STRICT"
    WARN = "WARN"
    REPAIR = "REPAIR"


class EventFlag(IntFlag):
    """Bitfield carried on a normalized event."""

    NONE = 0
    AGGRESSOR_BUY = 1 << 0
    AGGRESSOR_SELL = 1 << 1
    AUCTION = 1 << 2
    INTERMARKET_SWEEP = 1 << 3
    HIDDEN = 1 << 4
    REPAIRED = 1 << 5


class FillState(Enum):
    """Why a fill happened (or did not)."""

    MARKETABLE = "MARKETABLE"
    RESTING = "RESTING"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


class PlacementStyle(Enum):
    """How a child order seeks liquidity."""

    PASSIVE = "passive"
    AGGRESSIVE = "aggressive"
    ADAPTIVE = "adaptive"


class ReportType(Enum):
    """Execution report kinds flowing back to a policy."""

    ACCEPTED = "ACCEPTED"
    PARTIAL_FILL = "PARTIAL_FILL"
    FILL = "FILL"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class BenchmarkKind(Enum):
    ARRIVAL_PRICE = "arrival_price"
    INTERVAL_VWAP = "interval_vwap"
    INTERVAL_TWAP = "interval_twap"
    INTERVAL_MID = "interval_mid"
    CLOSE = "close"
