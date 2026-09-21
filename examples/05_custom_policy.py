"""Write a policy and run it.

A policy receives a `MarketState` and returns `ChildOrder`s. It has no reference
to the book, the clock, the queue model or the event stream, so "the strategy
reached into the simulator" is structurally impossible rather than merely
forbidden.

This example implements a liquidity-seeking policy that widens its participation
when the spread is tight and the book is deep, and backs off when either turns
hostile. It is a demonstration of the interface, not a strategy claim.

Run: python examples/05_custom_policy.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tradeforge.application.harness import ExecutionHarness
from tradeforge.costs.fees import FeeConfig, FeeModel
from tradeforge.data.registry import create_adapter
from tradeforge.domain.book import MarketState
from tradeforge.domain.orders import ChildOrder
from tradeforge.execution.guards import GuardConfig, Guardrails
from tradeforge.execution.oms import Oms
from tradeforge.execution.policies.base import ExecutionPolicyBase
from tradeforge.execution.simulator import ExecutionSimulator, SimulatorSettings
from tradeforge.infrastructure.config import load_configs, validate_required
from tradeforge.microstructure import MicrostructureEngine
from tradeforge.orderbook import BookSettings, create_book
from tradeforge.queue.factory import create_queue_model, queue_mode_from_config
from tradeforge.replay import ReplayRunner, ReplaySettings, SimulationClock, build_latency_model
from tradeforge.tca import MarketObserver, build_tca_report


class LiquiditySeekingPolicy(ExecutionPolicyBase):
    """Trade more when liquidity is cheap, less when it is not.

    Rules (deliberately simple, and stated so the result can be interpreted):

      * rebalance at most once per `rebalance_ns`;
      * target participation rises with the spread and with visible depth;
      * never exceed `max_slice_base` per slice;
      * sweep whatever is left at the window end.
    """

    def __init__(
        self,
        *,
        rebalance_ns: int = 60_000_000_000,
        base_rate: float = 0.04,
        max_slice_base: int = 2_000,
        depth_reference_base: int = 3_000,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        if not 0.0 < base_rate <= 1.0:
            raise ValueError("base_rate must be in (0, 1]")
        self._rebalance_ns = rebalance_ns
        self._base_rate = base_rate
        self._max_slice_base = max_slice_base
        self._depth_reference = depth_reference_base
        self._next_ns = self._parent.start_ns
        self._last_volume = 0
        self._slice_index = 0

    @property
    def name(self) -> str:
        return "liquidity_seeking"

    def on_market_event(self, state: MarketState) -> list[ChildOrder]:
        if state.timestamp_ns < self._next_ns:
            return []
        self._next_ns = state.timestamp_ns + self._rebalance_ns

        observed = max(state.market_volume_base - self._last_volume, 0)
        self._last_volume = state.market_volume_base
        if observed <= 0 or self.parent_remaining_base <= 0:
            return []

        rate = self._base_rate * self._liquidity_multiplier(state)
        desired = round(rate * observed)
        desired = min(desired, self._max_slice_base, self.parent_remaining_base)
        if desired <= 0:
            return []

        order = self._emit(state=state, quantity_base=desired, slice_index=self._slice_index)
        self._slice_index += 1
        return [order] if order else []

    def on_window_end(self, state: MarketState) -> list[ChildOrder]:
        if self._end_of_window != "sweep_marketable" or self.unfilled_base <= 0:
            return []
        order = self._aggressive(
            state=state, quantity_base=self.unfilled_base, slice_index=self._slice_index
        )
        return [order] if order else []

    def _liquidity_multiplier(self, state: MarketState) -> float:
        """1.0 when conditions are neutral; up to 2.5 when they are favourable."""
        if not state.has_two_sided_book:
            return 0.0
        depth = sum(level.quantity_base for level in state.bid_levels[:5]) + sum(
            level.quantity_base for level in state.ask_levels[:5]
        )
        depth_factor = min(depth / self._depth_reference, 2.0)

        spread = state.spread_ticks or 0
        # A wide spread makes passive placement more profitable per share, but
        # also means the touch is further away. Cap the benefit.
        spread_factor = 1.0 if spread <= 1 else min(1.0 + 0.15 * (spread - 1), 1.5)

        imbalance = 0.0
        if depth > 0:
            bid_depth = sum(level.quantity_base for level in state.bid_levels[:5])
            imbalance = abs(bid_depth / depth - 0.5) * 2.0
        # A lopsided book means adverse selection risk; back off.
        imbalance_factor = max(1.0 - 0.5 * imbalance, 0.4)

        return max(min(depth_factor * spread_factor * imbalance_factor, 2.5), 0.0)


def main() -> int:
    configs = load_configs(ROOT / "configs")
    validate_required(configs)

    # Build the stack by hand rather than through the harness, because the
    # harness only knows the policies in its registry. Everything else is the
    # same objects the harness would construct.
    harness = ExecutionHarness(configs)
    spec = harness.spec()
    parent = harness.parent_order(_request())
    book_cfg = configs["book"]
    replay_cfg = configs["replay"]

    book = create_book(
        symbol=spec.symbol,
        settings=BookSettings.from_dict(book_cfg),
        data_type=harness.data_type(),
        spec=spec,
    )
    oms = Oms()
    policy = LiquiditySeekingPolicy(
        parent=parent,
        spec=spec,
        oms=oms,
        style=harness.configs["execution"]["child_placement"]["default_style"],
        end_of_window="sweep_marketable",
    )
    mode, cancel_policy = queue_mode_from_config(configs["queue"], harness.data_type())
    clock = SimulationClock(parent.start_ns)
    horizons = harness.markout_horizons()
    from tradeforge.tca import max_markout_horizon_ns

    observer = MarketObserver(
        start_ns=parent.start_ns,
        end_ns=parent.end_ns + max_markout_horizon_ns(horizons),
    )

    simulator = ExecutionSimulator(
        parent=parent,
        spec=spec,
        oms=oms,
        policy=policy,  # type: ignore[arg-type]
        book=book,  # type: ignore[arg-type]
        queue_model=create_queue_model(mode, harness.data_type(), cancel_policy),
        latency=build_latency_model(configs["latency"]),
        guards=Guardrails(GuardConfig.from_dict(replay_cfg)),
        fees=FeeModel(FeeConfig.from_dict(configs["costs"])),
        clock=clock,
        settings=SimulatorSettings(
            snapshot_depth=int(book_cfg["book"]["snapshot_depth"]),
            counterfactual_mode=replay_cfg["replay"]["counterfactual_mode"],
        ),
    )

    runner = ReplayRunner(
        events=create_adapter(
            configs["market_data"]["source"]["adapter"],
            dict(configs["market_data"]["source"]["options"]),
        ).events(),
        book=book,
        features=MicrostructureEngine(),
        clock=clock,
        settings=ReplaySettings(
            snapshot_depth=int(book_cfg["book"]["snapshot_depth"]),
            stop_after_ns=parent.end_ns + max_markout_horizon_ns(horizons),
        ),
        observers=(observer,),
    )

    result = runner.run(simulator)
    report = build_tca_report(result, observer, spec, markout_horizons_ns=horizons)

    print("Custom policy: liquidity_seeking")
    print()
    print(
        f"  filled          {result.filled_base:,} / {result.requested_base:,} "
        f"({result.completion_rate:.2%})"
    )
    print(f"  average price   {result.avg_fill_price_ticks:.4f} ticks")
    print(f"  maker share     {result.maker_fill_ratio:.1%}")
    print(f"  child orders    {result.n_child_orders}")
    print(f"  participation   {result.participation_rate:.3%}")
    print()
    print(f"  vs arrival      {report.metrics.cost_vs_arrival_bps:+.4f} bps")
    print(f"  vs VWAP         {report.metrics.cost_vs_vwap_bps:+.4f} bps")
    print(f"  vs TWAP         {report.metrics.cost_vs_twap_bps:+.4f} bps")
    print()
    print("  Compare against the built-in policies with `make demo`.")
    print("  A custom policy is not evidence of anything until it has been")
    print("  compared on identical seeds, with intervals - see example 04.")
    return 0


def _request():
    from tradeforge.application.harness import RunRequest

    return RunRequest(policy="custom", style="passive", run_id="custom-policy")


if __name__ == "__main__":
    raise SystemExit(main())
