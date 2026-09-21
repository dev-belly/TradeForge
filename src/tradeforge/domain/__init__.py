"""Domain layer.

Pure business objects. Nothing here may import a framework, a database driver,
a web framework or a dataframe library - enforced by
`tests/architecture/test_dependency_direction.py`.
"""

from .book import BookSnapshot, MarketState, PriceLevel, TradeRecord
from .capability import Capability, require, supports
from .enums import (
    BenchmarkKind,
    BookMode,
    CancelAheadPolicy,
    DataType,
    EventFlag,
    EventType,
    ExecutionStatus,
    FillState,
    LiquidityFlag,
    OrderStatus,
    OrderType,
    PlacementStyle,
    QueueMode,
    ReportType,
    Side,
    TimeInForce,
    ValidationMode,
)
from .events import MarketEvent
from .exceptions import (
    AdapterError,
    BookIntegrityError,
    ConfigurationError,
    DataCapabilityError,
    EventValidationError,
    GuardrailBreachError,
    InstrumentError,
    LeakageError,
    MatchingError,
    OrderStateError,
    TradeForgeError,
)
from .fills import ExecutionReport, Fill, markout_bps, signed_cost_bps
from .instrument import InstrumentSpec
from .orders import (
    ALLOWED_ORDER_TRANSITIONS,
    ChildOrder,
    Order,
    ParentOrder,
)

__all__ = [
    "ALLOWED_ORDER_TRANSITIONS",
    "AdapterError",
    "BenchmarkKind",
    "BookIntegrityError",
    "BookMode",
    "BookSnapshot",
    "CancelAheadPolicy",
    "Capability",
    "ChildOrder",
    "ConfigurationError",
    "DataCapabilityError",
    "DataType",
    "EventFlag",
    "EventType",
    "EventValidationError",
    "ExecutionReport",
    "ExecutionStatus",
    "Fill",
    "FillState",
    "GuardrailBreachError",
    "InstrumentError",
    "InstrumentSpec",
    "LeakageError",
    "LiquidityFlag",
    "MarketEvent",
    "MarketState",
    "MatchingError",
    "Order",
    "OrderStateError",
    "OrderStatus",
    "OrderType",
    "ParentOrder",
    "PlacementStyle",
    "PriceLevel",
    "QueueMode",
    "ReportType",
    "Side",
    "TimeInForce",
    "TradeForgeError",
    "TradeRecord",
    "ValidationMode",
    "markout_bps",
    "require",
    "signed_cost_bps",
    "supports",
]
