"""Reconcile reported shortfall to cash costs, not only execution prices."""

from __future__ import annotations

from dataclasses import replace
from decimal import ROUND_UP, Decimal, Inexact, Rounded, localcontext

import pytest

from tradeforge.domain.book import MarketState, PriceLevel
from tradeforge.domain.enums import EventType, LiquidityFlag, Side
from tradeforge.domain.events import MarketEvent
from tradeforge.domain.fills import Fill
from tradeforge.domain.instrument import InstrumentSpec
from tradeforge.execution.result import ExecutionResult
from tradeforge.tca import MarketObserver, build_tca_report, compute_benchmarks

START = 34_200_000_000_000
END = START + 60_000_000_000


def _observe(observer, timestamp, mid):
    observer.observe(
        MarketEvent(
            sequence_id=timestamp,
            exchange_timestamp_ns=timestamp,
            symbol="TEST",
            event_type=EventType.TRADE,
            side=Side.BUY,
            price_ticks=mid,
            quantity_base=100,
            source="test",
        ),
        MarketState(
            symbol="TEST",
            timestamp_ns=timestamp,
            sequence_id=timestamp,
            best_bid_ticks=mid - 1,
            best_ask_ticks=mid + 1,
            best_bid_qty_base=500,
            best_ask_qty_base=500,
            bid_levels=(PriceLevel(mid - 1, 500),),
            ask_levels=(PriceLevel(mid + 1, 500),),
            last_trade_ticks=None,
            last_trade_qty_base=0,
            aggressor_side_known=False,
        ),
    )


def _case(*, side=Side.BUY, filled=100, price=10_000, fee="5.00", terminal=10_000):
    spec = InstrumentSpec("TEST", Decimal("0.01"), 1, "USD", 5_000, 20_000)
    observer = MarketObserver(START, END)
    _observe(observer, START, 10_000)
    _observe(observer, END, terminal)
    fees = Decimal(fee)
    fills = (
        (
            Fill(
                fill_id=1,
                order_id=1,
                client_order_id="c",
                parent_order_id="p",
                symbol="TEST",
                side=side,
                price_ticks=price,
                quantity_base=filled,
                timestamp_ns=START + 1,
                liquidity_flag=LiquidityFlag.TAKER,
                fee=fees,
                sequence_id=1,
            ),
        )
        if filled
        else ()
    )
    result = ExecutionResult(
        parent_order_id="p",
        symbol="TEST",
        side=side,
        policy_name="twap",
        requested_base=100,
        filled_base=filled,
        unfilled_base=100 - filled,
        start_ns=START,
        end_ns=END,
        arrival_mid_ticks=10_000,
        terminal_mid_ticks=terminal,
        avg_fill_price_ticks=float(price) if filled else None,
        notional=spec.notional(price, filled),
        fees_total=fees,
        fills=fills,
    )
    return result, observer, spec


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
@pytest.mark.parametrize("filled", [50, 100])
@pytest.mark.parametrize("fee", ["5.00", "-2.00"])
def test_flat_price_shortfall_is_the_fee_on_requested_arrival_notional(side, filled, fee):
    result, observer, spec = _case(side=side, filled=filled, fee=fee)
    report = build_tca_report(result, observer, spec)
    # Requested arrival notional is USD 100 x 100 = USD 10,000.
    # A USD 5 fee is 5 bps even when only half the order fills, on either side.
    expected = float(fee)
    assert report.metrics.implementation_shortfall_bps == pytest.approx(expected)
    assert report.metrics.price_shortfall_bps == pytest.approx(0)
    assert report.attribution.is_total_bps == pytest.approx(expected)
    assert report.attribution.is_filled_bps == pytest.approx(expected * 100 / filled)
    assert report.attribution.residual_impact_bps == pytest.approx(0)


@pytest.mark.parametrize("side", [Side.BUY, Side.SELL])
@pytest.mark.parametrize("price", [9_000, 11_000])
def test_fees_use_arrival_notional_and_do_not_change_sign_on_sell(side, price):
    result, observer, spec = _case(side=side, filled=50, price=price, terminal=10_200)
    report = build_tca_report(result, observer, spec)
    # 50 shares at USD 90 or 110, another 50 marked at USD 102, plus USD 5.
    expected_price = side.sign * ((price / 100 - 100) * 50 + 2 * 50)
    assert report.metrics.implementation_shortfall_bps == pytest.approx(expected_price + 5)
    assert report.metrics.price_shortfall_bps == pytest.approx(expected_price)
    assert report.attribution.is_total_bps == pytest.approx(expected_price + 5)
    assert report.attribution.fees_bps == pytest.approx(10)
    assert report.attribution.residual_impact_bps == pytest.approx(0)


@pytest.mark.parametrize("fee", ["5.00", "-2.00"])
def test_fee_attribution_ignores_hostile_decimal_context(fee):
    result, observer, spec = _case(filled=50, price=11_000, fee=fee)
    with localcontext() as ctx:
        ctx.prec = 2
        ctx.rounding = ROUND_UP
        ctx.Emax = 2
        ctx.Emin = -2
        ctx.traps[Inexact] = True
        ctx.traps[Rounded] = True
        report = build_tca_report(result, observer, spec)
    assert report.metrics.implementation_shortfall_bps == pytest.approx(500 + float(fee))
    assert report.attribution.fees_bps == pytest.approx(float(fee) * 2)


def test_unfilled_order_has_only_opportunity_cost():
    result, observer, spec = _case(filled=0, fee="0", terminal=10_200)
    report = build_tca_report(result, observer, spec)
    assert report.metrics.implementation_shortfall_bps == pytest.approx(200)
    assert report.attribution.is_total_bps == pytest.approx(200)
    assert report.attribution.fees_bps is None


@pytest.mark.parametrize("filled", [50, 100])
def test_missing_required_benchmark_does_not_turn_fees_into_complete_shortfall(filled):
    from tradeforge.tca import compute_attribution, compute_cost_metrics

    result, observer, spec = _case(filled=filled)
    benchmarks = compute_benchmarks(observer, start_ns=START, end_ns=END)
    no_arrival = replace(benchmarks, arrival_mid_ticks=None)
    assert compute_cost_metrics(result, no_arrival, spec).implementation_shortfall_bps is None
    assert compute_attribution(result, no_arrival, observer, spec).is_total_bps is None

    no_terminal = replace(benchmarks, terminal_mid_ticks=None)
    expected = None if filled == 50 else 5
    assert compute_cost_metrics(result, no_terminal, spec).implementation_shortfall_bps == expected
    assert compute_attribution(result, no_terminal, observer, spec).is_total_bps == expected


@pytest.mark.parametrize("filled", [50, 100])
def test_storage_and_sql_keep_fee_inclusive_shortfall(tmp_path, filled):
    import pyarrow.parquet as pq

    from tradeforge.storage import DuckDbStore, ParquetWriter

    result, observer, spec = _case(filled=filled)
    report = build_tca_report(result, observer, spec, markout_horizons_ns=(1,))
    writer = ParquetWriter(tmp_path)
    writer.write_tca(report, run_id="fee-check", seed=1)
    rows = pq.read_table(tmp_path / "tca_metrics").to_pylist()
    assert len(rows) == 1
    assert rows[0]["implementation_shortfall_bps"] == pytest.approx(5)
    assert rows[0]["fees_bps"] == pytest.approx(5 * 100 / filled)
    with DuckDbStore(tmp_path, sql_dir="sql") as store:
        row = store.run_named("03_cost_attribution").iloc[0]
    assert row["is_bps"] == pytest.approx(5)
    assert row["is_filled_bps"] == pytest.approx(5 * 100 / filled)
    assert row["residual_share_of_is"] == pytest.approx(0)


def test_report_discloses_cost_bases_and_exports_price_only_value():
    result, observer, spec = _case()
    report = build_tca_report(result, observer, spec)
    assert report.headline["price_shortfall_bps"] == 0
    assert report.to_dict()["metrics"]["price_shortfall_bps"] == 0
    assert report.provenance["shortfall_basis"] == "requested_arrival_notional_including_fees"
    assert report.provenance["attribution_basis"] == "filled_arrival_notional_including_fees"
