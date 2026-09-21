"""Run one parent order and read its full TCA report.

Run: python examples/02_run_one_execution.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tradeforge.application.harness import ExecutionHarness, RunRequest
from tradeforge.infrastructure.config import load_configs, validate_required


def main() -> int:
    configs = load_configs(ROOT / "configs")
    validate_required(configs)
    harness = ExecutionHarness(configs)

    context = harness.run(
        RunRequest(policy="twap", style="passive", seed=20260908, run_id="example")
    )
    result = context.result
    report = context.tca
    if result is None or report is None:
        print("the run produced no result")
        return 1

    print("=" * 72)
    print("PROVENANCE - read this before the numbers")
    print("=" * 72)
    for key in (
        "dataset_name",
        "provenance",
        "data_type",
        "queue_mode",
        "latency_basis",
        "counterfactual_mode",
        "config_fingerprint",
    ):
        print(f"  {key:<22} {context.provenance.get(key)}")
    print()

    print("=" * 72)
    print("EXECUTION")
    print("=" * 72)
    print(f"  requested          {result.requested_base:,} base units")
    print(f"  filled             {result.filled_base:,} ({result.completion_rate:.2%})")
    print(f"  average price      {result.avg_fill_price_ticks:.4f} ticks")
    print(f"  maker share        {result.maker_fill_ratio:.1%} of filled quantity")
    print(f"  participation      {result.participation_rate:.3%} of market volume")
    print(
        f"  child orders       {result.n_child_orders} "
        f"(rejects {result.n_rejects}, cancels {result.n_cancels})"
    )
    print(f"  notional           {result.notional}")
    print(f"  fees               {result.fees_total}")
    print()

    print("=" * 72)
    print("BENCHMARKS AND COST  (positive bps = worse)")
    print("=" * 72)
    metrics = report.metrics
    rows = [
        ("arrival mid", metrics.arrival_mid_ticks, metrics.cost_vs_arrival_bps),
        ("interval VWAP", metrics.interval_vwap_ticks, metrics.cost_vs_vwap_bps),
        ("interval TWAP", metrics.interval_twap_ticks, metrics.cost_vs_twap_bps),
        ("interval mid", metrics.interval_mid_ticks, metrics.cost_vs_interval_mid_bps),
        ("terminal mid", metrics.terminal_mid_ticks, metrics.cost_vs_terminal_bps),
    ]
    print(f"  {'benchmark':<16}{'price (ticks)':>15}{'cost (bps)':>14}")
    for name, price, cost in rows:
        price_text = f"{price:,.4f}" if price is not None else "-"
        cost_text = f"{cost:+.4f}" if cost is not None else "-"
        print(f"  {name:<16}{price_text:>15}{cost_text:>14}")
    print(f"  {'implementation':<16}{'':>15}{metrics.implementation_shortfall_bps:+.4f}")
    print()
    spread = max(c for _, _, c in rows if c is not None) - min(
        c for _, _, c in rows if c is not None
    )
    print(f"  The five benchmarks span {spread:.2f} bps on ONE execution.")
    print("  When arrival and VWAP disagree, arrival is crediting the strategy")
    print("  with market drift, not execution skill.")
    print()

    print("=" * 72)
    print("ATTRIBUTION  (additive by construction)")
    print("=" * 72)
    attribution = report.attribution
    for name, value in (
        ("is_filled", attribution.is_filled_bps),
        ("spread cost", attribution.spread_cost_bps),
        ("fees", attribution.fees_bps),
        ("timing", attribution.timing_bps),
        ("residual (unexplained)", attribution.residual_impact_bps),
        ("opportunity cost", attribution.opportunity_cost_bps),
        ("is_total", attribution.is_total_bps),
    ):
        text = f"{value:+.5f}" if value is not None else "-"
        print(f"  {name:<24}{text:>12}")
    if attribution.residual_share is not None:
        print(f"\n  residual share of is_filled: {attribution.residual_share:.2%}")
        print("  The residual is NOT a measured market impact. It absorbs the")
        print("  denominator approximation and unmodelled impact. This replay")
        print("  never altered the tape, so impact cannot be identified from it.")
    print()

    print("=" * 72)
    print("MARKOUTS  (positive bps = adverse)")
    print("=" * 72)
    print(f"  {'horizon':>9}{'measurable':>12}{'coverage':>10}{'vw (bps)':>12}{'median':>10}")
    for summary in report.markouts:
        vw = (
            f"{summary.volume_weighted_bps:+.4f}"
            if summary.volume_weighted_bps is not None
            else "-"
        )
        med = f"{summary.median_bps:+.4f}" if summary.median_bps is not None else "-"
        print(
            f"  {summary.horizon_ns / 1e9:>8.0f}s{summary.n_measurable:>12}"
            f"{summary.coverage:>9.0%}{vw:>12}{med:>10}"
        )
    print()
    print("  A short-horizon negative turning positive at longer horizons is the")
    print("  signature of adverse selection on passive fills.")
    print()

    print("=" * 72)
    print("CAVEATS")
    print("=" * 72)
    for caveat in report.caveats:
        print(f"  - {caveat}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
