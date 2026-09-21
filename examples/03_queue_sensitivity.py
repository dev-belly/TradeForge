"""How much does the L2 queue assumption move the answer?

With aggregated level data the queue position is unobservable. The honest
treatment is not to pick one assumption and call it exact, but to vary it and
report the spread. That spread is the error bar on any passive-execution claim
made from L2 data.

If the spread is larger than the difference between two strategies, the
strategies are not distinguishable at this data tier - whatever the point
estimates say.

Run: python examples/03_queue_sensitivity.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tradeforge.application.harness import ExecutionHarness, RunRequest
from tradeforge.infrastructure.config import load_configs, validate_required

POLICIES = ("optimistic", "neutral", "conservative")


def main() -> int:
    configs = load_configs(ROOT / "configs")
    validate_required(configs)
    harness = ExecutionHarness(configs)

    print("SYNTHETIC data. Queue mode: APPROXIMATE (L2/MBP source).")
    print()
    print(
        f"{'queue policy':<16}{'fill':>9}{'maker':>9}{'IS (bps)':>12}"
        f"{'vs VWAP':>10}{'spread bps':>12}"
    )
    print("-" * 68)

    values: list[float] = []
    for policy in POLICIES:
        context = harness.run(
            RunRequest(
                policy="twap",
                style="passive",
                queue_policy=policy,
                seed=20260908,
                run_id=f"queue-{policy}",
            )
        )
        metrics = context.tca.metrics
        values.append(metrics.implementation_shortfall_bps or 0.0)
        print(
            f"{policy:<16}{metrics.fill_ratio:>8.2%}{metrics.maker_fill_ratio:>9.1%}"
            f"{metrics.implementation_shortfall_bps:>+12.4f}"
            f"{metrics.cost_vs_vwap_bps:>+10.4f}"
            f"{'':>12}"
        )

    spread = max(values) - min(values)
    print("-" * 68)
    print(f"{'policy spread':<16}{'':>9}{'':>9}{'':>12}{'':>10}{spread:>12.4f}")
    print()
    print(f"The three assumptions differ by {spread:.4f} bps on this execution.")
    print()
    print("What each policy assumes about a level that shrank without a trade:")
    print("  optimistic    the removed shares were BEHIND us  -> we move up")
    print("  neutral       split in proportion to our visible share")
    print("  conservative  the removed shares were AHEAD of us -> we move back")
    print()
    print("None of them is 'the right one'. L2 data does not contain the answer,")
    print("and the platform refuses to pretend otherwise.")
    print()

    # The same comparison on aggressive placement, for contrast: an aggressive
    # order crosses, so the queue assumption should barely matter.
    print("For contrast, the same grid with AGGRESSIVE placement:")
    print()
    print(f"{'queue policy':<16}{'fill':>9}{'maker':>9}{'IS (bps)':>12}")
    print("-" * 46)
    aggressive_values: list[float] = []
    for policy in POLICIES:
        context = harness.run(
            RunRequest(
                policy="twap",
                style="aggressive",
                queue_policy=policy,
                seed=20260908,
                run_id=f"queue-agg-{policy}",
            )
        )
        metrics = context.tca.metrics
        aggressive_values.append(metrics.implementation_shortfall_bps or 0.0)
        print(
            f"{policy:<16}{metrics.fill_ratio:>8.2%}{metrics.maker_fill_ratio:>9.1%}"
            f"{metrics.implementation_shortfall_bps:>+12.4f}"
        )

    aggressive_spread = max(aggressive_values) - min(aggressive_values)
    print("-" * 46)
    print(f"{'policy spread':<16}{'':>9}{'':>9}{aggressive_spread:>+12.4f}")
    print()
    print(f"Passive spread {spread:.4f} bps vs aggressive spread {aggressive_spread:.4f} bps.")
    print("The queue assumption is a passive-execution problem; crossing the")
    print("spread does not depend on where we were standing in the queue.")
    print()
    print("=" * 68)
    print("DO NOT OVER-READ THIS")
    print("=" * 68)
    print("The passive spread is small here because the synthetic generator")
    print("trades at the touch often enough that passive orders fill almost")
    print("completely under every assumption. When the fill ratio is near 100%,")
    print("the attribution policy has little left to decide.")
    print()
    print("On real data, or on a generator calibrated to a quiet venue, a level")
    print("that shrinks mostly ahead of you can cost you the fill entirely - and")
    print("then the spread between these three numbers is the whole answer.")
    print("The query to watch is sql/05_queue_policy_sensitivity.sql, which")
    print("reports the spread next to the fill ratio so the two are read together.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
