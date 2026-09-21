"""Why a point estimate is not a result.

Runs the algorithm grid across several sessions and reports intervals rather
than means. Pairing is by seed, so two strategies in the same repetition see the
same market and the difference between them is not contaminated by session
variance.

The expected outcome on the bundled generator is that nothing is significant,
and the script says so rather than quoting whichever mean happened to be lowest.

Run: python examples/04_paired_comparison.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tradeforge.application.harness import ExecutionHarness
from tradeforge.infrastructure.config import load_configs, validate_required
from tradeforge.research import build_experiment_specs, run_experiment


def main() -> int:
    configs = load_configs(ROOT / "configs")
    validate_required(configs)
    harness = ExecutionHarness(configs)

    base_seed = int(configs["market_data"]["source"]["options"].get("seed", 20260908))
    seeds = [base_seed + i for i in range(3)]
    spec = build_experiment_specs(configs)["A_algorithm_comparison"]

    print(f"Experiment: {spec.name}")
    print(f"Metric:     {spec.metric}")
    print(f"Sessions:   {seeds}  (paired by seed)")
    print()

    result = run_experiment(harness, spec, seeds=seeds, n_resamples=500)

    print(
        f"{'cell':<14}{'mean':>10}{'ci low':>10}{'ci high':>10}"
        f"{'fill':>8}{'participation':>15}{'maker':>8}"
    )
    print("-" * 75)
    for cell in result.cells:
        print(
            f"{cell.label:<14}{cell.ci.estimate:>+10.4f}{cell.ci.lower:>+10.4f}"
            f"{cell.ci.upper:>+10.4f}{cell.mean_fill_ratio:>8.2%}"
            f"{cell.mean_participation_rate:>15.3%}{cell.mean_maker_ratio:>8.1%}"
        )
    print()

    print("Paired comparisons against the baseline (negative = cheaper):")
    print(
        f"{'cell':<14}{'vs':<6}{'diff':>9}{'ci low':>10}{'ci high':>10}"
        f"{'W/L':>8}{'p':>8}{'holm':>8}{'excl 0':>8}"
    )
    print("-" * 81)
    for comparison in result.comparisons:
        print(
            f"{comparison.label_a:<14}{comparison.label_b:<6}"
            f"{comparison.mean_difference:>+9.4f}{comparison.ci.lower:>+10.4f}"
            f"{comparison.ci.upper:>+10.4f}"
            f"{f'{comparison.wins_a}/{comparison.wins_b}':>8}"
            f"{comparison.sign_test_p:>8.3f}"
            f"{(comparison.holm_adjusted_p or 1.0):>8.3f}"
            f"{comparison.significant!s:>8}"
        )
    print()

    significant = [c for c in result.comparisons if c.significant]
    print("=" * 75)
    if significant:
        print(f"{len(significant)} comparison(s) have an interval excluding zero.")
        print("That is a statement about the interval, not a verdict about which")
        print("algorithm is better. Check the effect size and the wins/losses.")
    else:
        print("No comparison has an interval excluding zero.")
        print()
        print("This is the honest result, not a failure. With three sessions the")
        print("interval is wider than any difference the algorithms produce, so")
        print("the correct conclusion is that they are indistinguishable here.")
        print("Quoting the lowest mean would be a claim the data does not support.")
    print()
    print("Statistics used:")
    print("  moving block bootstrap over sessions (unit = one independent session)")
    print("  exact two-sided binomial sign test on the non-tied pairs")
    print("  Holm-Bonferroni across the whole family of comparisons")
    print("  pairing by seed, so both arms see the same market")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
