"""Run a grid of executions and store the artefacts as Parquet.

This is what populates the DuckDB views: without it the seven SQL queries have
nothing to read. The grid is small on purpose - it exists so `make run-all`
produces a queryable store in a couple of minutes, not so it produces a
publication.

Every row carries the config fingerprint, the seed and the environment, so a
number found in a query can always be traced back to the run that made it.

Run: `make run-all`
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tradeforge.application.harness import ExecutionHarness, RunRequest
from tradeforge.infrastructure.config import load_configs, validate_required
from tradeforge.research.registry import EnvironmentRecord
from tradeforge.storage import ParquetWriter

DEFAULT_POLICIES = ("twap", "vwap", "pov", "is_baseline")
DEFAULT_STYLES = ("passive", "aggressive")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="artifacts/runs")
    parser.add_argument("--configs", default=str(ROOT / "configs"))
    parser.add_argument("--policies", default=",".join(DEFAULT_POLICIES))
    parser.add_argument("--styles", default=",".join(DEFAULT_STYLES))
    parser.add_argument(
        "--seeds",
        type=int,
        default=2,
        help="Sessions per cell. Cells are paired by seed, so more seeds is the "
        "only way to get a usable interval.",
    )
    parser.add_argument("--queue-policies", default="", help="Comma-separated queue overrides.")
    parser.add_argument("--latencies", default="", help="Comma-separated latency overrides, ns.")
    args = parser.parse_args()

    configs = load_configs(args.configs)
    validate_required(configs)
    harness = ExecutionHarness(configs)
    writer = ParquetWriter(args.output)

    dataset = configs["market_data"]["dataset"]
    writer.write_dataset(
        dataset_name=str(dataset["name"]),
        dataset_version=str(dataset["version"]),
        data_type=str(dataset["data_type"]),
        provenance=str(dataset["provenance"]),
        redistributable=bool(dataset.get("redistributable", False)),
        license_name=dataset.get("license"),
        config_fingerprint=harness.fingerprint,
    )

    base_seed = int(configs["market_data"]["source"]["options"].get("seed", 20260908))
    seeds = [base_seed + i for i in range(max(args.seeds, 1))]
    policies = [p.strip() for p in args.policies.split(",") if p.strip()]
    styles = [s.strip() for s in args.styles.split(",") if s.strip()]
    queue_policies = [q.strip() for q in args.queue_policies.split(",") if q.strip()] or [None]
    latencies = [int(value) for value in args.latencies.split(",") if value.strip()] or [None]

    environment = EnvironmentRecord.capture(ROOT)
    print(f"SYNTHETIC data | commit {environment.git_commit} (dirty={environment.git_dirty})")
    print(
        f"grid: {len(policies)} policies x {len(styles)} styles x "
        f"{len(queue_policies)} queue x {len(latencies)} latency x {len(seeds)} seeds"
    )
    print()

    n_runs = 0
    for seed in seeds:
        for policy in policies:
            for style in styles:
                for queue_policy in queue_policies:
                    for latency_ns in latencies:
                        suffix = "-".join(
                            part
                            for part in (
                                policy,
                                style,
                                queue_policy or "",
                                f"lat{latency_ns}" if latency_ns is not None else "",
                                f"s{seed}",
                            )
                            if part
                        )
                        run_id = suffix
                        context = harness.run(
                            RunRequest(
                                policy=policy,
                                style=style,
                                seed=seed,
                                queue_policy=queue_policy,
                                latency_ns=latency_ns,
                                run_id=run_id,
                            )
                        )
                        if context.result is None or context.tca is None:
                            print(f"  {run_id}: no result, skipped")
                            continue

                        result = context.result
                        writer.write_execution(
                            result,
                            run_id=run_id,
                            seed=seed,
                            config_fingerprint=harness.fingerprint,
                        )
                        writer.write_child_orders(result.child_orders, run_id=run_id, seed=seed)
                        writer.write_fills(result.fills, run_id=run_id, seed=seed)
                        writer.write_tca(context.tca, run_id=run_id, seed=seed)

                        metrics = context.tca.metrics
                        n_runs += 1
                        print(
                            f"  {run_id:<44} fill {metrics.fill_ratio:>6.2%} "
                            f"maker {metrics.maker_fill_ratio:>6.1%} "
                            f"IS {metrics.implementation_shortfall_bps:>+8.3f} bps"
                        )

    print()
    print(f"{n_runs} run(s) written to {args.output}")
    print("Query them with: make db-list && make db-query NAME=01_execution_summary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
