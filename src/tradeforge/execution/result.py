"""The output contract of one simulated parent order.

An `ExecutionResult` is a value object: it carries no simulator handles, no live
book reference and no event stream. Downstream consumers (TCA, reports, SQL,
dashboard) can only see what is here, which is why every field that describes
*how* the result was produced (`queue_mode`, `latency_basis`,
`counterfactual_mode`) is mandatory rather than optional metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from ..domain.enums import LiquidityFlag, Side
from ..domain.fills import ExecutionReport, Fill
from ..domain.orders import ChildOrder


@dataclass(frozen=True)
class ExecutionResult:
    """Everything one simulated execution produced, and how it was produced."""

    parent_order_id: str
    symbol: str
    side: Side
    policy_name: str

    requested_base: int
    filled_base: int
    unfilled_base: int

    start_ns: int
    end_ns: int

    arrival_mid_ticks: float | None
    terminal_mid_ticks: float | None
    avg_fill_price_ticks: float | None

    notional: Decimal
    fees_total: Decimal

    fills: tuple[Fill, ...] = ()
    reports: tuple[ExecutionReport, ...] = ()
    child_orders: tuple[ChildOrder, ...] = ()

    market_volume_base: int = 0
    n_child_orders: int = 0
    n_rejects: int = 0
    n_cancels: int = 0

    # Provenance: what the numbers actually mean.
    queue_mode: str = "APPROXIMATE"
    latency_basis: str = "scenario"
    counterfactual_mode: str = "replay_approximation"
    metadata: dict[str, object] = field(default_factory=dict)

    # ----------------------------------------------------------- derived

    @property
    def completion_rate(self) -> float:
        if self.requested_base <= 0:
            return 0.0
        return self.filled_base / self.requested_base

    @property
    def duration_ns(self) -> int:
        return self.end_ns - self.start_ns

    def filled_base_by_liquidity(self, flag: LiquidityFlag) -> int:
        return sum(f.quantity_base for f in self.fills if f.liquidity_flag is flag)

    @property
    def maker_filled_base(self) -> int:
        return self.filled_base_by_liquidity(LiquidityFlag.MAKER)

    @property
    def taker_filled_base(self) -> int:
        return self.filled_base_by_liquidity(LiquidityFlag.TAKER)

    @property
    def maker_fill_ratio(self) -> float:
        if self.filled_base <= 0:
            return 0.0
        return self.maker_filled_base / self.filled_base

    @property
    def participation_rate(self) -> float:
        if self.market_volume_base <= 0:
            return 0.0
        return self.filled_base / self.market_volume_base

    def to_dict(self) -> dict[str, object]:
        """Flat summary row. Fills/reports are exported separately."""
        return {
            "parent_order_id": self.parent_order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "policy": self.policy_name,
            "requested_base": self.requested_base,
            "filled_base": self.filled_base,
            "unfilled_base": self.unfilled_base,
            "completion_rate": self.completion_rate,
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "duration_ns": self.duration_ns,
            "arrival_mid_ticks": self.arrival_mid_ticks,
            "terminal_mid_ticks": self.terminal_mid_ticks,
            "avg_fill_price_ticks": self.avg_fill_price_ticks,
            "notional": float(self.notional),
            "fees_total": float(self.fees_total),
            "maker_filled_base": self.maker_filled_base,
            "taker_filled_base": self.taker_filled_base,
            "maker_fill_ratio": self.maker_fill_ratio,
            "market_volume_base": self.market_volume_base,
            "participation_rate": self.participation_rate,
            "n_child_orders": self.n_child_orders,
            "n_rejects": self.n_rejects,
            "n_cancels": self.n_cancels,
            "queue_mode": self.queue_mode,
            "latency_basis": self.latency_basis,
            "counterfactual_mode": self.counterfactual_mode,
        }
