"""Transaction cost analysis: benchmarks, metrics, attribution, markouts.

The properties asserted here are the ones that make a TCA number trustworthy:
benchmarks are computed only from data inside the window, the attribution is
additive, and markouts use the system-wide sign convention.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from tradeforge.domain.book import MarketState, PriceLevel
from tradeforge.domain.enums import BenchmarkKind, EventType, LiquidityFlag, Side
from tradeforge.domain.events import MarketEvent
from tradeforge.domain.fills import Fill
from tradeforge.domain.instrument import InstrumentSpec
from tradeforge.execution.result import ExecutionResult
from tradeforge.tca import (
    MarketObserver,
    build_tca_report,
    compute_attribution,
    compute_benchmarks,
    compute_cost_metrics,
    compute_markouts,
)

START = 34_200_000_000_000
END = START + 60_000_000_000


def _state(ts, bid, ask, bid_qty=500, ask_qty=500) -> MarketState:
    return MarketState(
        symbol="TEST",
        timestamp_ns=ts,
        sequence_id=ts,
        best_bid_ticks=bid,
        best_ask_ticks=ask,
        best_bid_qty_base=bid_qty,
        best_ask_qty_base=ask_qty,
        bid_levels=(PriceLevel(bid, bid_qty),),
        ask_levels=(PriceLevel(ask, ask_qty),),
        last_trade_ticks=None,
        last_trade_qty_base=0,
        aggressor_side_known=False,
    )


def _trade_event(ts, price, quantity, aggressor=Side.BUY) -> MarketEvent:
    from tradeforge.domain.enums import EventFlag

    return MarketEvent(
        sequence_id=ts,
        exchange_timestamp_ns=ts,
        symbol="TEST",
        event_type=EventType.TRADE,
        side=aggressor,
        price_ticks=price,
        quantity_base=quantity,
        flags=(EventFlag.AGGRESSOR_BUY if aggressor is Side.BUY else EventFlag.AGGRESSOR_SELL),
        source="test",
    )


@pytest.fixture
def observer():
    obs = MarketObserver(start_ns=START, end_ns=END)
    # Mids: 10000 for 20s, then 10010 for 20s, then 10020 for 20s.
    for step in range(0, 60, 5):
        ts = START + step * 1_000_000_000
        mid = 10_000 + 10 * (step // 20)
        obs.observe(_trade_event(ts, mid, 100), _state(ts, mid - 1, mid + 1))
    return obs


class TestBenchmarks:
    def test_vwap_is_the_volume_weighted_trade_price(self):
        obs = MarketObserver(start_ns=START, end_ns=END)
        obs.observe(_trade_event(START, 10_000, 300), _state(START, 9_999, 10_001))
        obs.observe(_trade_event(START + 1, 10_100, 100), _state(START + 1, 10_099, 10_101))
        prices = compute_benchmarks(obs, start_ns=START, end_ns=END)
        assert prices.interval_vwap_ticks == pytest.approx((300 * 10_000 + 100 * 10_100) / 400)

    def test_twap_is_time_weighted_not_event_weighted(self):
        """Busy periods must not dominate a TWAP.

        Here the market spends 90 seconds at 10000 and 10 seconds at 11000, but
        produces many more observations at 11000. A plain mean would be dragged
        toward 11000; the time-weighted mean must not be.
        """
        obs = MarketObserver(start_ns=START, end_ns=START + 100_000_000_000)
        for ts in range(0, 90, 10):
            obs.observe(
                _trade_event(START + ts * 1_000_000_000, 10_000, 1),
                _state(START + ts * 1_000_000_000, 9_999, 10_001),
            )
        for ts in range(90, 100, 1):
            obs.observe(
                _trade_event(START + ts * 1_000_000_000, 11_000, 1),
                _state(START + ts * 1_000_000_000, 10_999, 11_001),
            )
        prices = compute_benchmarks(obs, start_ns=START, end_ns=START + 100_000_000_000)
        assert prices.interval_twap_ticks is not None
        assert prices.interval_twap_ticks < 10_200
        assert prices.interval_mid_ticks > prices.interval_twap_ticks

    def test_arrival_is_the_first_mid_at_or_after_the_window(self, observer):
        prices = compute_benchmarks(observer, start_ns=START, end_ns=END)
        assert prices.arrival_mid_ticks == 10_000.0

    def test_events_outside_the_window_are_ignored(self):
        obs = MarketObserver(start_ns=START, end_ns=END)
        obs.observe(_trade_event(START - 1_000, 5_000, 100), _state(START - 1_000, 4_999, 5_001))
        obs.observe(_trade_event(END + 1_000, 99_000, 100), _state(END + 1_000, 98_999, 99_001))
        obs.observe(_trade_event(START + 1, 10_000, 100), _state(START + 1, 9_999, 10_001))
        prices = compute_benchmarks(obs, start_ns=START, end_ns=END)
        assert prices.arrival_mid_ticks == 10_000.0
        assert prices.n_trade_prints == 1

    def test_markout_observations_cannot_change_execution_benchmarks(self):
        obs = MarketObserver(start_ns=START - 1, end_ns=END + 1)
        obs.observe(_trade_event(START - 1, 50_000, 500), _state(START - 1, 49_999, 50_001))
        obs.observe(_trade_event(START, 10_000, 100), _state(START, 9_999, 10_001))
        obs.observe(_trade_event(END, 10_100, 300), _state(END, 10_099, 10_101))
        obs.observe(_trade_event(END + 1, 80_000, 1_000), _state(END + 1, 79_999, 80_001))

        prices = compute_benchmarks(obs, start_ns=START, end_ns=END)
        assert prices.arrival_mid_ticks == 10_000
        assert prices.terminal_mid_ticks == 10_100
        assert prices.interval_vwap_ticks == pytest.approx(10_075)
        assert prices.interval_twap_ticks == pytest.approx(10_050)
        assert prices.interval_mid_ticks == pytest.approx(10_050)
        assert prices.n_mid_observations == prices.n_trade_prints == 2
        assert prices.window_volume_base == 400

    def test_no_in_window_observation_gives_no_price_benchmark(self):
        obs = MarketObserver(start_ns=START, end_ns=END + 1)
        obs.observe(_trade_event(END + 1, 10_000, 100), _state(END + 1, 9_999, 10_001))
        prices = compute_benchmarks(obs, start_ns=START, end_ns=END)
        assert prices.arrival_mid_ticks is None
        assert prices.terminal_mid_ticks is None
        assert prices.interval_vwap_ticks is None
        assert prices.n_mid_observations == prices.n_trade_prints == 0

    def test_benchmark_lookup_by_kind(self, observer):
        prices = compute_benchmarks(observer, start_ns=START, end_ns=END)
        assert prices.value(BenchmarkKind.ARRIVAL_PRICE) == 10_000.0
        assert prices.value(BenchmarkKind.INTERVAL_VWAP) is not None
        assert prices.value(BenchmarkKind.CLOSE) == prices.terminal_mid_ticks

    def test_coverage_is_reported(self, observer):
        prices = compute_benchmarks(observer, start_ns=START, end_ns=END)
        coverage = prices.coverage()
        assert coverage["n_mid_observations"] == 12
        assert coverage["n_trade_prints"] == 12
        assert coverage["vwap_available"] is True


class TestMarkouts:
    def test_missing_horizon_is_not_imputed_as_zero(self):
        """A markout we cannot measure must be absent, not flat."""
        obs = MarketObserver(start_ns=START, end_ns=END)
        obs.observe(_trade_event(START, 10_000, 100), _state(START, 9_999, 10_001))
        fill = Fill(
            fill_id=1,
            order_id=1,
            client_order_id="c",
            parent_order_id="p",
            symbol="TEST",
            side=Side.BUY,
            price_ticks=10_001,
            quantity_base=100,
            timestamp_ns=START,
            liquidity_flag=LiquidityFlag.TAKER,
            fee=Decimal("0"),
            sequence_id=1,
        )
        # A one-hour horizon with one minute of data: unmeasurable.
        summaries = compute_markouts((fill,), obs, (3_600_000_000_000,))
        assert summaries[0].n_measurable == 0
        assert summaries[0].volume_weighted_bps is None
        assert summaries[0].coverage == 0.0

    def test_adverse_markout_is_positive(self):
        obs = MarketObserver(start_ns=START, end_ns=END + 60_000_000_000)
        obs.observe(_trade_event(START, 10_000, 100), _state(START, 9_999, 10_001))
        later = START + 30_000_000_000
        # Mid falls after we bought.
        obs.observe(_trade_event(later, 9_900, 100), _state(later, 9_899, 9_901))
        fill = Fill(
            fill_id=1,
            order_id=1,
            client_order_id="c",
            parent_order_id="p",
            symbol="TEST",
            side=Side.BUY,
            price_ticks=10_001,
            quantity_base=100,
            timestamp_ns=START,
            liquidity_flag=LiquidityFlag.TAKER,
            fee=Decimal("0"),
            sequence_id=1,
        )
        summaries = compute_markouts((fill,), obs, (30_000_000_000,))
        assert summaries[0].volume_weighted_bps > 0

    def test_favourable_markout_is_negative(self):
        obs = MarketObserver(start_ns=START, end_ns=END + 60_000_000_000)
        obs.observe(_trade_event(START, 10_000, 100), _state(START, 9_999, 10_001))
        later = START + 30_000_000_000
        obs.observe(_trade_event(later, 10_100, 100), _state(later, 10_099, 10_101))
        fill = Fill(
            fill_id=1,
            order_id=1,
            client_order_id="c",
            parent_order_id="p",
            symbol="TEST",
            side=Side.BUY,
            price_ticks=10_001,
            quantity_base=100,
            timestamp_ns=START,
            liquidity_flag=LiquidityFlag.TAKER,
            fee=Decimal("0"),
            sequence_id=1,
        )
        summaries = compute_markouts((fill,), obs, (30_000_000_000,))
        assert summaries[0].volume_weighted_bps < 0


class TestMetricsAndAttribution:
    def _result(self, avg_fill=10_005.0, filled=1_000, requested=1_000) -> ExecutionResult:
        return ExecutionResult(
            parent_order_id="p",
            symbol="TEST",
            side=Side.BUY,
            policy_name="twap",
            requested_base=requested,
            filled_base=filled,
            unfilled_base=requested - filled,
            start_ns=START,
            end_ns=END,
            arrival_mid_ticks=10_000.0,
            terminal_mid_ticks=10_020.0,
            avg_fill_price_ticks=avg_fill,
            notional=Decimal("100050.00"),
            fees_total=Decimal("10.00"),
            fills=(),
            market_volume_base=100_000,
        )

    def test_cost_signs_are_signed_by_side(self, observer):
        benchmarks = compute_benchmarks(observer, start_ns=START, end_ns=END)
        spec = InstrumentSpec("TEST", Decimal("0.01"), 1, "USD", 5_000, 20_000)

        buy = compute_cost_metrics(self._result(), benchmarks, spec)
        assert buy.cost_vs_arrival_bps > 0  # bought above arrival

        sell_result = self._result()
        object.__setattr__(sell_result, "side", Side.SELL)
        sell = compute_cost_metrics(sell_result, benchmarks, spec)
        assert sell.cost_vs_arrival_bps < 0  # selling above arrival is good

    def test_implementation_shortfall_charges_the_unfilled_part(self, observer):
        benchmarks = compute_benchmarks(observer, start_ns=START, end_ns=END)
        spec = InstrumentSpec("TEST", Decimal("0.01"), 1, "USD", 5_000, 20_000)
        # Terminal mid (10020) is above arrival (10000): for a BUY the unfilled
        # part is a real cost, so a partial fill must score worse than the same
        # execution priced only on the filled part.
        partial = compute_cost_metrics(self._result(filled=500), benchmarks, spec)
        full = compute_cost_metrics(self._result(filled=1_000), benchmarks, spec)
        assert partial.implementation_shortfall_bps != full.implementation_shortfall_bps

    def test_unmeasurable_shortfall_is_not_reported_as_zero(self, observer):
        benchmarks = compute_benchmarks(observer, start_ns=START, end_ns=END)
        spec = InstrumentSpec("TEST", Decimal("0.01"), 1, "USD", 5_000, 20_000)
        no_close = replace(benchmarks, terminal_mid_ticks=None)
        partial = self._result(filled=500)
        metrics = compute_cost_metrics(partial, no_close, spec)
        attribution = compute_attribution(partial, no_close, observer, spec)
        assert metrics.implementation_shortfall_bps is None
        assert attribution.is_total_bps is None

        no_fill = self._result(avg_fill=None, filled=0)
        assert compute_cost_metrics(no_fill, no_close, spec).implementation_shortfall_bps is None
        assert compute_attribution(no_fill, no_close, observer, spec).is_total_bps is None

        complete = self._result(filled=1_000)
        complete_metrics = compute_cost_metrics(complete, no_close, spec)
        assert complete_metrics.implementation_shortfall_bps is not None
        assert compute_attribution(complete, no_close, observer, spec).is_total_bps is not None

        opportunity_only = compute_cost_metrics(no_fill, benchmarks, spec)
        assert opportunity_only.implementation_shortfall_bps is not None
        attribution = compute_attribution(no_fill, benchmarks, observer, spec)
        assert attribution.is_total_bps == pytest.approx(
            opportunity_only.implementation_shortfall_bps
        )

    def test_attribution_is_additive(self, observer):
        benchmarks = compute_benchmarks(observer, start_ns=START, end_ns=END)
        spec = InstrumentSpec("TEST", Decimal("0.01"), 1, "USD", 5_000, 20_000)
        attribution = compute_attribution(self._result(), benchmarks, observer, spec)
        if attribution.residual_impact_bps is None:
            pytest.skip("no fills to attribute")
        total = (
            attribution.spread_cost_bps
            + attribution.fees_bps
            + attribution.timing_bps
            + attribution.residual_impact_bps
        )
        assert total == pytest.approx(attribution.is_filled_bps, abs=1e-9)

    def test_attribution_labels_the_residual_as_unexplained(self, observer):
        benchmarks = compute_benchmarks(observer, start_ns=START, end_ns=END)
        spec = InstrumentSpec("TEST", Decimal("0.01"), 1, "USD", 5_000, 20_000)
        attribution = compute_attribution(self._result(), benchmarks, observer, spec)
        payload = attribution.to_dict()
        assert "not a measured impact" in payload["residual_impact_label"]

    def test_fees_bps_is_negative_for_a_net_rebate(self, observer):
        benchmarks = compute_benchmarks(observer, start_ns=START, end_ns=END)
        spec = InstrumentSpec("TEST", Decimal("0.01"), 1, "USD", 5_000, 20_000)
        result = self._result()
        object.__setattr__(result, "fees_total", Decimal("-5.00"))
        attribution = compute_attribution(result, benchmarks, observer, spec)
        assert attribution.fees_bps < 0


class TestReport:
    def test_report_carries_provenance_and_caveats(self, observer):
        from tradeforge.domain.fills import Fill

        result = ExecutionResult(
            parent_order_id="p",
            symbol="TEST",
            side=Side.BUY,
            policy_name="twap",
            requested_base=100,
            filled_base=100,
            unfilled_base=0,
            start_ns=START,
            end_ns=END,
            arrival_mid_ticks=10_000.0,
            terminal_mid_ticks=10_020.0,
            avg_fill_price_ticks=10_005.0,
            notional=Decimal("10005.00"),
            fees_total=Decimal("1.00"),
            fills=(
                Fill(
                    fill_id=1,
                    order_id=1,
                    client_order_id="c",
                    parent_order_id="p",
                    symbol="TEST",
                    side=Side.BUY,
                    price_ticks=10_005,
                    quantity_base=100,
                    timestamp_ns=START + 1_000_000_000,
                    liquidity_flag=LiquidityFlag.MAKER,
                    fee=Decimal("1.00"),
                    sequence_id=1,
                ),
            ),
            queue_mode="APPROXIMATE",
            latency_basis="scenario",
        )
        spec = InstrumentSpec("TEST", Decimal("0.01"), 1, "USD", 5_000, 20_000)
        report = build_tca_report(result, observer, spec, provenance={"dataset_name": "x"})

        assert report.provenance["queue_mode"] == "APPROXIMATE"
        assert report.provenance["counterfactual_mode"] == "replay_approximation"
        joined = " ".join(report.caveats)
        assert "counterfactual" in joined
        assert "post-fill data" in joined
        # Approximate queue must be declared in the caveats.
        assert "ESTIMATE" in joined

    def test_exact_queue_does_not_claim_an_estimate(self, observer):
        result = ExecutionResult(
            parent_order_id="p",
            symbol="TEST",
            side=Side.BUY,
            policy_name="twap",
            requested_base=100,
            filled_base=0,
            unfilled_base=100,
            start_ns=START,
            end_ns=END,
            arrival_mid_ticks=10_000.0,
            terminal_mid_ticks=10_020.0,
            avg_fill_price_ticks=None,
            notional=Decimal("0"),
            fees_total=Decimal("0"),
            queue_mode="EXACT",
        )
        spec = InstrumentSpec("TEST", Decimal("0.01"), 1, "USD", 5_000, 20_000)
        report = build_tca_report(result, observer, spec)
        assert not any("ESTIMATE" in c for c in report.caveats)
