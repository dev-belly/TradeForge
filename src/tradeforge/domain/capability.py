"""Data capability enforcement.

A feature that requires a capability the active source does not provide must
refuse to run - never silently degrade into a guess. See
`docs/data-capabilities.md`.
"""

from __future__ import annotations

from enum import Enum

from .enums import DataType
from .exceptions import DataCapabilityError


class Capability(Enum):
    BEST_BID_ASK = "best_bid_ask"
    QUOTED_SPREAD = "quoted_spread"
    MID_PRICE = "mid_price"
    MICROPRICE = "microprice"
    TOP_IMBALANCE = "top_imbalance"
    DEPTH_CURVE = "depth_curve"
    MULTILEVEL_IMBALANCE = "multilevel_imbalance"
    DEPTH_SLOPE = "depth_slope"
    MBP_RECONSTRUCTION = "mbp_reconstruction"
    MBO_RECONSTRUCTION = "mbo_reconstruction"
    EXACT_QUEUE = "exact_queue"
    APPROXIMATE_QUEUE = "approximate_queue"
    ORDER_LEVEL_CANCEL = "order_level_cancel"
    CANCELLATION_INTENSITY = "cancellation_intensity"
    ORDER_FLOW_IMBALANCE = "order_flow_imbalance"
    TRADE_AGGRESSOR = "trade_aggressor"
    REALIZED_VOLATILITY = "realized_volatility"
    MARKET_VWAP = "market_vwap"


_TIER_RANK: dict[DataType, int] = {
    DataType.L1: 0,
    DataType.L2_MBP: 1,
    DataType.L3_MBO: 2,
}

_REQUIRED_TIER: dict[Capability, DataType] = {
    Capability.BEST_BID_ASK: DataType.L1,
    Capability.QUOTED_SPREAD: DataType.L1,
    Capability.MID_PRICE: DataType.L1,
    Capability.MICROPRICE: DataType.L1,
    Capability.TOP_IMBALANCE: DataType.L1,
    Capability.DEPTH_CURVE: DataType.L2_MBP,
    Capability.MULTILEVEL_IMBALANCE: DataType.L2_MBP,
    Capability.DEPTH_SLOPE: DataType.L2_MBP,
    Capability.MBP_RECONSTRUCTION: DataType.L2_MBP,
    Capability.MBO_RECONSTRUCTION: DataType.L3_MBO,
    Capability.EXACT_QUEUE: DataType.L3_MBO,
    Capability.APPROXIMATE_QUEUE: DataType.L2_MBP,
    Capability.ORDER_LEVEL_CANCEL: DataType.L3_MBO,
    Capability.CANCELLATION_INTENSITY: DataType.L2_MBP,
    Capability.ORDER_FLOW_IMBALANCE: DataType.L2_MBP,
    Capability.TRADE_AGGRESSOR: DataType.L3_MBO,
    Capability.REALIZED_VOLATILITY: DataType.L1,
    Capability.MARKET_VWAP: DataType.L1,
}

# Capabilities that only make sense with a specific tier (not "at least").
_EXACT_TIER_ONLY: frozenset[Capability] = frozenset(
    {Capability.MBO_RECONSTRUCTION, Capability.EXACT_QUEUE, Capability.ORDER_LEVEL_CANCEL}
)


def supports(data_type: DataType, capability: Capability) -> bool:
    """Whether `data_type` can legitimately support `capability`."""
    required = _REQUIRED_TIER[capability]
    if capability in _EXACT_TIER_ONLY:
        return data_type is required
    return _TIER_RANK[data_type] >= _TIER_RANK[required]


def require(data_type: DataType, capability: Capability, context: str = "") -> None:
    """Raise `DataCapabilityError` unless the capability is genuinely available."""
    if not supports(data_type, capability):
        suffix = f" ({context})" if context else ""
        raise DataCapabilityError(
            f"{capability.value} requires at least {_REQUIRED_TIER[capability].value} data, "
            f"active source declares {data_type.value}{suffix}. "
            "This is a hard stop: TradeForge does not fabricate market detail."
        )


def describe(data_type: DataType) -> dict[str, bool]:
    """Capability matrix row, used by reports and `data inspect`."""
    return {cap.value: supports(data_type, cap) for cap in Capability}
