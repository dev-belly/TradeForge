"""Execution simulator: clock + latency + queue + OMS + book.

This is the only place where "our order meets the market". Rules enforced here,
and nowhere else:

  1. A decision at `t` reaches the venue at `latency.arrival_ns(t)`. Between
     those two instants the market keeps moving. Nothing sleeps.
  2. An aggressive order pays the *arrival-time* book, against a limit fixed at
     *decision time*. That asymmetry is the whole point of modelling latency.
  3. A passive order joins the back of the queue at the arrival-time touch and
     can only be filled by a trade that consumes everything ahead of it.
     **Price touch alone never fills** - there is no code path in which
     "best bid >= our price" produces a fill.
  4. Simulated fills do not remove liquidity from the replayed book. Our orders
     were not in the historical stream, so removing depth would double-count.
     This is the documented `replay_approximation` counterfactual.

Fill attribution of trades with an unknown aggressor is skipped rather than
guessed: from an unattributed print we cannot say which side was consumed.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from ..domain.book import BookSnapshot, MarketState
from ..domain.enums import (
    EventType,
    LiquidityFlag,
    OrderStatus,
    OrderType,
    PlacementStyle,
    ReportType,
    Side,
    TimeInForce,
)
from ..domain.events import MarketEvent
from ..domain.exceptions import MatchingError
from ..domain.fills import ExecutionReport
from ..domain.instrument import InstrumentSpec
from ..domain.orders import ChildOrder, ParentOrder
from ..domain.protocols import Clock, LatencyModel, QueueModel
from ..matching.engine import match_order, resting_price_ticks
from .guards import Guardrails
from .oms import Oms
from .policies.base import ExecutionPolicyBase
from .result import ExecutionResult


class SupportsLevelSize(Protocol):
    """The book query the simulator needs before an event mutates a level."""

    def level_size_base(self, side: Side, price_ticks: int) -> int: ...

    def snapshot(self, depth: int | None = None) -> BookSnapshot: ...


@dataclass(frozen=True)
class SimulatorSettings:
    """Tunable behaviour. No market constants live here."""

    snapshot_depth: int = 10
    passive_offset_ticks: int = 0
    max_levels_crossed: int | None = None
    adaptive_min_spread_ticks: int = 2
    counterfactual_mode: str = "replay_approximation"
    cancel_working_at_end: bool = True
    # Cancel/repost horizon for resting passive orders. 0 disables it.
    # The cancel is applied at the decision instant: cancel latency is not
    # modelled (see docs/design-review.md, known limitations).
    passive_timeout_ns: int = 0
    # A CLEAR (followed by SNAPSHOT) makes level contents unknowable: our queue
    # position cannot be tracked across it, so passive orders are pulled.
    cancel_on_book_clear: bool = True


class ExecutionSimulator:
    """Runs one parent order against a replayed book."""

    def __init__(
        self,
        *,
        parent: ParentOrder,
        spec: InstrumentSpec,
        oms: Oms,
        policy: ExecutionPolicyBase,
        book: SupportsLevelSize,
        queue_model: QueueModel,
        latency: LatencyModel,
        guards: Guardrails,
        fees: FeeModelLike,
        clock: Clock,
        settings: SimulatorSettings | None = None,
    ) -> None:
        self._parent = parent
        self._spec = spec
        self._oms = oms
        self._policy = policy
        self._book = book
        self._queue = queue_model
        self._latency = latency
        self._guards = guards
        self._fees = fees
        self._clock = clock
        self._settings = settings or SimulatorSettings()

        self._reports: list[ExecutionReport] = []
        self._submitted: list[ChildOrder] = []
        self._decision_ns: dict[str, int] = {}
        self._filled_base = 0
        self._fees_total = Decimal("0")
        self._notional = Decimal("0")
        self._market_volume_base = 0
        self._n_rejects = 0
        self._n_cancels = 0
        self._arrival_mid_ticks: float | None = None
        self._terminal_mid_ticks: float | None = None
        self._arrival_ns: int | None = None
        self._unattributed_trades = 0
        self._window_closed = False
        self._last_state_ns: int | None = None

    # ------------------------------------------------------------ properties

    @property
    def parent(self) -> ParentOrder:
        return self._parent

    @property
    def policy_name(self) -> str:
        return self._policy.name

    @property
    def filled_base(self) -> int:
        return self._filled_base

    @property
    def market_volume_base(self) -> int:
        return self._market_volume_base

    @property
    def reports(self) -> tuple[ExecutionReport, ...]:
        return tuple(self._reports)

    # --------------------------------------------------------- market events

    def observe_event(self, event: MarketEvent) -> None:
        """Called with the book in its PRE-event state (level sizes still old)."""
        if event.event_type is EventType.TRADE:
            self._observe_trade(event)
        elif event.event_type in (EventType.CANCEL, EventType.MODIFY, EventType.REPLACE):
            self._observe_size_decrease(event)
        elif event.event_type is EventType.CLEAR and self._settings.cancel_on_book_clear:
            self._on_book_clear(event)

    def _observe_trade(self, event: MarketEvent) -> None:
        if event.price_ticks is None or not event.quantity_base:
            return
        self._market_volume_base += event.quantity_base
        aggressor = event.aggressor_side() or event.side
        if aggressor is None:
            # Unattributed print: we cannot say which side was consumed, so no
            # queue is consumed either. Conservative and honest.
            self._unattributed_trades += 1
            return
        resting = aggressor.opposite
        fills = self._queue.on_trade(
            price_ticks=event.price_ticks,
            quantity_base=event.quantity_base,
            order_id=event.order_id,
            side=resting,
        )
        for client_order_id, filled in fills:
            self._fill_from_queue(
                client_order_id,
                filled,
                at_ns=event.exchange_timestamp_ns,
                sequence_id=event.sequence_id,
            )

    def _observe_size_decrease(self, event: MarketEvent) -> None:
        if event.price_ticks is None or event.side is None or not event.quantity_base:
            return
        level_before = self._book.level_size_base(event.side, event.price_ticks)
        if level_before <= 0:
            return
        removed = min(event.quantity_base, level_before)
        # Exact models raise DataCapabilityError when order identity is missing;
        # that is a configuration bug and must surface, not be swallowed.
        self._queue.on_size_decrease(
            price_ticks=event.price_ticks,
            removed_base=removed,
            level_size_before=level_before,
            order_id=event.order_id,
            side=event.side,
        )

    def _on_book_clear(self, event: MarketEvent) -> None:
        for order in tuple(self._oms.open_orders):
            if order.placed_price_ticks is None:
                continue
            self._release(order)
            report = self._oms.cancel(
                order, at_ns=event.exchange_timestamp_ns, reason="book cleared"
            )
            self._record(report)
            self._n_cancels += 1

    # ------------------------------------------------------------ decisions

    def observe_state(self, state: MarketState, snapshot: BookSnapshot) -> None:
        """Feed the policy and submit whatever it decides, through latency."""
        if state.market_volume_base > self._market_volume_base:
            self._market_volume_base = state.market_volume_base
        if state.has_two_sided_book:
            if self._arrival_mid_ticks is None:
                self._arrival_mid_ticks = state.mid_ticks
                self._arrival_ns = state.timestamp_ns
            self._terminal_mid_ticks = state.mid_ticks
        self._last_state_ns = state.timestamp_ns

        if self._window_closed:
            return

        decision = self._guards.check_participation(self._filled_base, self._market_volume_base)
        if not decision.allowed:
            self._guards.trip(decision.reason)
            self._cancel_all(state.timestamp_ns, reason=decision.reason)
            self._window_closed = True
            return

        if state.timestamp_ns >= self._parent.end_ns:
            self._close_window(state, snapshot)
            return

        self._expire_stale_passive(state.timestamp_ns)
        for order in self._policy.on_market_event(state):
            self.submit(order, decision_ns=state.timestamp_ns, snapshot=snapshot)

    def _expire_stale_passive(self, at_ns: int) -> None:
        """Cancel/repost: a passive order resting past the horizon is pulled.

        The unfilled quantity is not lost - it returns to the parent's remaining
        pool and the next slice picks it up through the cumulative-target rule.
        """
        timeout = self._settings.passive_timeout_ns
        if timeout <= 0:
            return
        for order in tuple(self._oms.open_orders):
            joined = order.queue_join_ns
            if joined is None or at_ns - joined < timeout:
                continue
            self._release(order)
            report = self._oms.cancel(
                order, at_ns=at_ns, reason=f"passive timeout after {timeout}ns"
            )
            self._record(report)
            self._n_cancels += 1

    def _close_window(self, state: MarketState, snapshot: BookSnapshot) -> None:
        """End of the parent window: pull working orders, then sweep.

        Order matters. Working orders are cancelled FIRST so the policy sees an
        accurate remainder; otherwise the sweep would be computed against
        quantity that is about to be pulled and would double-count it.
        """
        self._window_closed = True
        if self._policy.end_of_window != "leave":
            self._cancel_all(state.timestamp_ns, reason="parent window closed")
        for order in self._policy.on_window_end(state):
            self.submit(order, decision_ns=state.timestamp_ns, snapshot=snapshot)

    @property
    def is_finished(self) -> bool:
        """True once the window is closed and nothing is in flight or resting."""
        return self._window_closed and self._clock.pending == 0 and self._oms.n_open_orders == 0

    def submit(self, order: ChildOrder, *, decision_ns: int, snapshot: BookSnapshot) -> None:
        """Validate, price, and schedule the arrival of one child order."""
        self._submitted.append(order)
        self._decision_ns[order.client_order_id] = decision_ns

        allowed = self._guards.check_new_order(
            order,
            spec=self._spec,
            open_orders=self._oms.n_open_orders + self._clock.pending,
            inventory_base=self._oms.inventory_base,
        )
        if not allowed.allowed:
            self._n_rejects += 1
            report = self._oms.reject(order, at_ns=decision_ns, reason=allowed.reason)
            self._record(report)
            return

        limit = self._decision_limit_ticks(order, snapshot)
        if limit is None and order.style is not PlacementStyle.PASSIVE:
            self._n_rejects += 1
            report = self._oms.reject(
                order, at_ns=decision_ns, reason="no two-sided book at decision time"
            )
            self._record(report)
            return
        order.price_ticks = limit

        arrival_ns = self._latency.arrival_ns(decision_ns)
        self._clock.schedule(
            arrival_ns,
            lambda at_ns, bound=order: self._arrive(bound, at_ns),
            label=f"arrive:{order.client_order_id}",
        )

    def _decision_limit_ticks(self, order: ChildOrder, snapshot: BookSnapshot) -> int | None:
        """Limit price fixed at DECISION time; the fill happens at arrival time."""
        if order.style is PlacementStyle.PASSIVE:
            return None  # priced on arrival, where the queue actually is
        touch = snapshot.touch_ticks(order.side)
        if touch is None:
            return None
        if order.style is PlacementStyle.ADAPTIVE:
            spread = snapshot.spread_ticks or 0
            if spread >= self._settings.adaptive_min_spread_ticks:
                return None  # room to join inside: behave passively
        return touch

    # -------------------------------------------------------------- arrival

    def _arrive(self, order: ChildOrder, arrival_ns: int) -> None:
        snapshot = self._book.snapshot(self._settings.snapshot_depth)
        if order.style is PlacementStyle.AGGRESSIVE or (
            order.style is PlacementStyle.ADAPTIVE and order.price_ticks is not None
        ):
            self._arrive_aggressive(order, arrival_ns, snapshot)
        else:
            self._arrive_passive(order, arrival_ns, snapshot)

    def _arrive_aggressive(
        self, order: ChildOrder, arrival_ns: int, snapshot: BookSnapshot
    ) -> None:
        accept = self._oms.accept(order, at_ns=arrival_ns)
        self._record(accept)
        try:
            result = match_order(
                snapshot,
                side=order.side,
                quantity_base=order.remaining_base,
                order_type=OrderType.LIMIT,
                limit_price_ticks=order.price_ticks,
                time_in_force=order.time_in_force,
                max_levels_crossed=self._settings.max_levels_crossed,
            )
        except MatchingError as exc:
            report = self._oms.cancel(order, at_ns=arrival_ns, reason=str(exc))
            self._record(report)
            return

        for matched in result.matched:
            self._apply_fill(
                order,
                quantity_base=matched.quantity_base,
                price_ticks=matched.price_ticks,
                at_ns=arrival_ns,
                liquidity=LiquidityFlag.TAKER,
                sequence_id=snapshot.sequence_id,
            )
        if order.remaining_base > 0:
            if order.time_in_force in (TimeInForce.IOC, TimeInForce.FOK):
                self._n_cancels += 1
                report = self._oms.cancel(
                    order, at_ns=arrival_ns, reason="IOC remainder not filled"
                )
                self._record(report)
                return
            # DAY/GTC: the unfilled remainder rests at its limit price.
            self._join_queue(order, arrival_ns, snapshot, price_ticks=order.price_ticks)

    def _arrive_passive(self, order: ChildOrder, arrival_ns: int, snapshot: BookSnapshot) -> None:
        price = resting_price_ticks(snapshot, order.side, self._settings.passive_offset_ticks)
        if price is None:
            accept = self._oms.accept(order, at_ns=arrival_ns)
            self._record(accept)
            self._n_cancels += 1
            report = self._oms.cancel(
                order,
                at_ns=arrival_ns,
                reason=f"empty {order.side.value} side: cannot post passively",
            )
            self._record(report)
            return
        accept = self._oms.accept(order, at_ns=arrival_ns)
        self._record(accept)
        self._join_queue(order, arrival_ns, snapshot, price_ticks=price)

    def _join_queue(
        self, order: ChildOrder, arrival_ns: int, snapshot: BookSnapshot, *, price_ticks: int
    ) -> None:
        level_size = self._book.level_size_base(order.side, price_ticks)
        order.placed_price_ticks = price_ticks
        order.queue_join_ns = arrival_ns
        order.estimated_queue_ahead_base = level_size
        self._queue.register(
            order.client_order_id,
            side=order.side,
            price_ticks=price_ticks,
            quantity_base=order.remaining_base,
            level_size_base=level_size,
        )

    # ---------------------------------------------------------------- fills

    def _fill_from_queue(
        self, client_order_id: str, quantity_base: int, *, at_ns: int, sequence_id: int
    ) -> None:
        """A passive fill: the queue ahead was consumed by an observed trade."""
        if quantity_base <= 0:
            return
        try:
            order = self._oms.order(client_order_id)
        except KeyError:
            return
        if order.status.is_terminal:
            return
        price = order.placed_price_ticks
        if price is None:
            return
        filled = min(quantity_base, order.remaining_base)
        wait_ns = at_ns - (order.queue_join_ns or at_ns)
        self._apply_fill(
            order,
            quantity_base=filled,
            price_ticks=price,
            at_ns=at_ns,
            liquidity=LiquidityFlag.MAKER,
            sequence_id=sequence_id,
            queue_wait_ns=wait_ns,
        )

    def _apply_fill(
        self,
        order: ChildOrder,
        *,
        quantity_base: int,
        price_ticks: int,
        at_ns: int,
        liquidity: LiquidityFlag,
        sequence_id: int,
        queue_wait_ns: int = 0,
    ) -> None:
        if quantity_base <= 0:
            return
        notional = self._spec.notional(price_ticks, quantity_base)
        fee = self._fees.fee(notional=notional, liquidity=liquidity, quantity_base=quantity_base)
        report = self._oms.fill(
            order,
            quantity_base=quantity_base,
            price_ticks=price_ticks,
            at_ns=at_ns,
            liquidity=liquidity,
            fee=fee,
            sequence_id=sequence_id,
            queue_wait_ns=queue_wait_ns,
        )
        self._record(report)
        self._filled_base += quantity_base
        self._notional += notional
        self._fees_total += fee
        self._parent.filled_base += quantity_base
        if order.status is OrderStatus.FILLED:
            self._release(order)

    def _release(self, order: ChildOrder) -> None:
        with contextlib.suppress(KeyError):
            self._queue.release(order.client_order_id)

    def _record(self, report: ExecutionReport) -> None:
        self._reports.append(report)
        if report.report_type in (ReportType.FILL, ReportType.PARTIAL_FILL):
            self._policy.on_fill(report)
        elif report.report_type is ReportType.REJECTED:
            self._policy.on_reject(report)
        elif report.report_type is ReportType.CANCELLED:
            self._policy.on_cancel(report)

    # ------------------------------------------------------------ finalizing

    def _cancel_all(self, at_ns: int, *, reason: str) -> None:
        for order in tuple(self._oms.open_orders):
            self._release(order)
            report = self._oms.cancel(order, at_ns=at_ns, reason=reason)
            self._record(report)
            self._n_cancels += 1

    def finalize(self, state: MarketState | None = None) -> ExecutionResult:
        """Close out working orders and freeze the result."""
        if state is not None and state.has_two_sided_book:
            self._terminal_mid_ticks = state.mid_ticks
        # The benchmark window is the parent's decision window, not wherever the
        # event stream happened to stop.
        end_ns = self._parent.end_ns
        if not self._window_closed and self._settings.cancel_working_at_end:
            self._cancel_all(end_ns, reason="parent order window closed")

        fills = self._oms.fills
        avg_price = None
        if self._filled_base > 0:
            notional_ticks = sum(f.price_ticks * f.quantity_base for f in fills)
            avg_price = notional_ticks / self._filled_base

        return ExecutionResult(
            parent_order_id=self._parent.parent_order_id,
            symbol=self._parent.symbol,
            side=self._parent.side,
            policy_name=self._policy.name,
            requested_base=self._parent.quantity_base,
            filled_base=self._filled_base,
            unfilled_base=max(self._parent.quantity_base - self._filled_base, 0),
            start_ns=self._parent.start_ns,
            end_ns=end_ns,
            arrival_mid_ticks=self._arrival_mid_ticks,
            terminal_mid_ticks=self._terminal_mid_ticks,
            avg_fill_price_ticks=avg_price,
            notional=self._notional,
            fees_total=self._fees_total,
            fills=fills,
            reports=tuple(self._reports),
            child_orders=tuple(self._submitted),
            market_volume_base=self._market_volume_base,
            n_child_orders=len(self._submitted),
            n_rejects=self._n_rejects,
            n_cancels=self._n_cancels,
            queue_mode=self._queue.mode.value,
            latency_basis=self._latency.basis,
            counterfactual_mode=self._settings.counterfactual_mode,
            metadata={
                "unattributed_trade_events": self._unattributed_trades,
                "guard_breaches": list(self._guards.breaches),
                "guard_tripped": self._guards.tripped,
                "snapshot_depth": self._settings.snapshot_depth,
                "arrival_ns": self._arrival_ns,
                "last_observed_ns": self._last_state_ns,
                "end_of_window": self._policy.end_of_window,
            },
        )


class FeeModelLike(Protocol):
    """Only the fee call the simulator needs."""

    def fee(
        self, *, notional: Decimal, liquidity: LiquidityFlag, quantity_base: int = 0
    ) -> Decimal: ...
