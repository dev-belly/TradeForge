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
from tradeforge.data.registry import create_adapter
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
    parser.add_argument(
        "--skip-events",
        action="store_true",
        help="Do not persist the event stream. Query 07 will then report the "
        "events table as missing rather than reporting zero events.",
    )
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

    # Query 07 reads `events`, and every other query is only interpretable in
    # the light of it, so the store is incomplete without the stream that was
    # replayed. One session is enough to answer "what data tier produced this,
    # how many events, how many trades" - writing every seed would multiply the
    # store by the seed count for no extra information.
    dataset_name = str(dataset["name"])
    if not args.skip_events:
        stream = create_adapter(
            configs["market_data"]["source"]["adapter"],
            dict(configs["market_data"]["source"].get("options", {})),
        )
        written = writer.write_events(stream.events(), dataset_name=dataset_name, seed=seeds[0])
        print(f"events: {written.n_rows:,} rows for seed {seeds[0]} -> {written.path.name}")

    n_runs = 0
    experiment_rows: list[dict[str, object]] = []
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

                        # `experiment_runs` is what makes the reproducibility
                        # question answerable: query 07 counts runs whose
                        # recorded commit came from a dirty tree, which means the
                        # commit does not fully describe the code that ran.
                        experiment_rows.append(
                            _experiment_row(
                                run_id=run_id,
                                seed=seed,
                                policy=policy,
                                style=style,
                                metric="implementation_shortfall_bps",
                                value=metrics.implementation_shortfall_bps,
                                harness_fingerprint=harness.fingerprint,
                                environment=environment,
                            )
                        )
                        print(
                            f"  {run_id:<44} fill {metrics.fill_ratio:>6.2%} "
                            f"maker {metrics.maker_fill_ratio:>6.1%} "
                            f"IS {metrics.implementation_shortfall_bps:>+8.3f} bps"
                        )

    if experiment_rows:
        written = writer.write_experiment_rows(experiment_rows)
        print(f"experiment_runs: {written.n_rows} rows -> {written.path.name}")

    print()
    print(f"{n_runs} run(s) written to {args.output}")
    print("Query them with: make db-list && make db-query NAME=01_execution_summary")
    return 0


def _experiment_row(
    *,
    run_id: str,
    seed: int,
    policy: str,
    style: str,
    metric: str,
    value: float,
    harness_fingerprint: str,
    environment: EnvironmentRecord,
) -> dict[str, object]:
    import json
    from datetime import UTC, datetime

    return {
        "experiment": "run_all",
        "cell": f"{policy}-{style}",
        "seed": seed,
        "metric": metric,
        "metric_value": float(value),
        "config_fingerprint": harness_fingerprint,
        "git_commit": environment.git_commit,
        "git_dirty": bool(environment.git_dirty),
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "tags_json": json.dumps({"run_id": run_id}, sort_keys=True),
    }


if __name__ == "__main__":
    raise SystemExit(main())
