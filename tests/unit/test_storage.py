"""Storage: the Parquet write path, the DuckDB read path, and the SQL queries.

This suite exists because the storage layer had **no tests at all** while seven
hand-written SQL queries depended on it. One of them, `07_data_quality_summary`,
read an `events` table that nothing ever wrote, so `make db-query
NAME=07_data_quality_summary` died with a DuckDB `CatalogException`. Every other
query worked, which is exactly why nobody noticed.

The load-bearing test is `TestPackagedQueries::test_every_query_runs`: it builds
a populated store and executes all seven. A column rename or a dropped table
anywhere in the pipeline now fails here instead of in a user's terminal.
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tradeforge.storage import TABLE_DDL, TABLE_ORDER, DuckDbStore, ParquetWriter

pytest.importorskip("pyarrow", reason="the Parquet write path needs pyarrow")
pytest.importorskip("duckdb", reason="the query layer needs duckdb")

#: Stores are built under the repository's gitignored `artifacts/` tree rather
#: than pytest's temporary directory, because some sandboxes deny writes outside
#: the workspace.
STORE_ROOT = Path("artifacts/test-stores")


@pytest.fixture(scope="module")
def populated_store(configs) -> Iterator[Path]:
    """A store holding every table, built by the real write path.

    Deliberately small - two policies, one style, one seed - so the suite stays
    quick. The point is that each table is non-empty and queryable, not that the
    numbers are interesting.
    """
    from tradeforge.application.harness import ExecutionHarness, RunRequest
    from tradeforge.data.registry import create_adapter

    root = STORE_ROOT / uuid.uuid4().hex[:12]
    root.mkdir(parents=True, exist_ok=True)
    try:
        harness = ExecutionHarness(configs)
        writer = ParquetWriter(root)
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

        source_config = configs["market_data"]["source"]
        options = dict(source_config.get("options", {}))
        options["n_events"] = 6_000
        seed = int(options.get("seed", 20260908))
        stream = create_adapter(source_config["adapter"], options)
        writer.write_events(stream.events(), dataset_name=str(dataset["name"]), seed=seed)

        experiment_rows = []
        for policy in ("twap", "pov"):
            context = harness.run(RunRequest(policy=policy, style="passive", seed=seed))
            assert context.result is not None and context.tca is not None
            writer.write_execution(
                context.result,
                run_id=policy,
                seed=seed,
                config_fingerprint=harness.fingerprint,
            )
            writer.write_child_orders(context.result.child_orders, run_id=policy, seed=seed)
            writer.write_fills(context.result.fills, run_id=policy, seed=seed)
            writer.write_tca(context.tca, run_id=policy, seed=seed)
            experiment_rows.append(
                {
                    "experiment": "test",
                    "cell": policy,
                    "seed": seed,
                    "metric": "implementation_shortfall_bps",
                    "metric_value": float(context.tca.metrics.implementation_shortfall_bps),
                    "config_fingerprint": harness.fingerprint,
                    "git_commit": "0" * 40,
                    "git_dirty": False,
                    "recorded_at_utc": datetime.now(UTC).isoformat(),
                    "tags_json": json.dumps({"run_id": policy}),
                }
            )
        writer.write_experiment_rows(experiment_rows)
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.fixture(scope="module")
def store(populated_store, project_root) -> Iterator[DuckDbStore]:
    store = DuckDbStore(populated_store, sql_dir=project_root / "sql")
    try:
        yield store
    finally:
        store.close()


class TestSchema:
    def test_every_declared_table_has_ddl(self):
        for table in TABLE_ORDER:
            assert table in TABLE_DDL, f"{table} is in TABLE_ORDER but has no DDL"

    def test_ddl_and_order_agree(self):
        assert set(TABLE_DDL) == set(TABLE_ORDER)

    def test_ddl_is_creatable(self, populated_store):
        """Every statement must be valid SQL, not just present."""
        store = DuckDbStore(populated_store)
        try:
            store.ensure_created()
        finally:
            store.close()

    #: How each table reaches a `config_fingerprint`. Roots hold it; children
    #: join to a root. A table absent from this map is untraceable.
    TRACEABILITY = {
        "datasets": "config_fingerprint",
        "events": "dataset_name -> datasets",
        "executions": "config_fingerprint",
        "child_orders": "run_id+seed -> executions",
        "fills": "run_id+seed -> executions",
        "tca_metrics": "run_id+seed -> executions",
        "markouts": "run_id+seed -> executions",
        "experiment_runs": "config_fingerprint",
    }

    def test_every_table_can_be_traced_to_a_configuration(self):
        """A row that cannot be traced to its configuration is not evidence.

        Not "every table has the column" - that was the docstring's claim, and it
        was false for five of the eight. The real rule is that every table
        reaches a fingerprinted root, directly or through a join key that exists.
        """
        assert set(self.TRACEABILITY) == set(TABLE_ORDER), (
            "a table was added or removed without deciding how it is traced"
        )
        for table, route in self.TRACEABILITY.items():
            if route == "config_fingerprint":
                assert "config_fingerprint" in TABLE_DDL[table], (
                    f"{table} is a root but carries no config_fingerprint"
                )
                continue
            match = re.match(r"([\w+]+)\s*->\s*(\w+)$", route)
            assert match, f"cannot parse the trace route for {table}: {route!r}"
            columns, target = match.group(1).split("+"), match.group(2)
            for column in columns:
                assert column in TABLE_DDL[table], (
                    f"{table} is traced via {route} but has no {column} column"
                )
            for column in columns:
                assert column in TABLE_DDL[target], (
                    f"{table} joins to {target} on {column}, which {target} lacks"
                )

    def test_the_trace_graph_has_a_root_for_every_child(self):
        """No table may depend on another child, only on a root.

        A chain (a child joining to a child) would mean a rename two levels down
        silently breaks traceability for everything above it.
        """
        roots = {t for t, route in self.TRACEABILITY.items() if route == "config_fingerprint"}
        children = set(TABLE_ORDER) - roots
        for table in children:
            target = re.search(r"->\s*(\w+)$", self.TRACEABILITY[table]).group(1)
            assert target in roots, (
                f"{table} joins to {target}, which is itself a child; trace routes "
                "must terminate at a fingerprinted root"
            )


class TestWritePath:
    def test_the_store_is_complete(self, store):
        """Every table the schema declares should be populated by the writers.

        This is the assertion that would have caught the missing `events` table:
        the schema declared it, a writer existed for it, and nothing called it.
        """
        status = store.status()
        assert not status.missing_tables, (
            f"declared but never written: {status.missing_tables}. Either write "
            "them or remove them from TABLE_ORDER - a table the schema promises "
            "and nothing fills makes every query that reads it a trap."
        )

    def test_write_results_report_their_row_counts(self, populated_store):
        writer = ParquetWriter(populated_store)
        from tradeforge.domain.enums import EventType, Side
        from tradeforge.domain.events import MarketEvent

        result = writer.write_events(
            [
                MarketEvent(
                    sequence_id=1,
                    exchange_timestamp_ns=1,
                    symbol="TEST",
                    event_type=EventType.ADD,
                    side=Side.BUY,
                    price_ticks=10_000,
                    quantity_base=100,
                    source="test",
                )
            ],
            dataset_name="round-trip",
            seed=1,
        )
        assert result.table == "events"
        assert result.n_rows == 1
        assert result.path.exists()

    def test_parquet_round_trips_through_duckdb(self, populated_store):
        store = DuckDbStore(populated_store)
        try:
            rows = store.query("SELECT COUNT(*) FROM events")
        finally:
            store.close()
        assert rows[0][0] > 0


class TestPackagedQueries:
    """Every query in `sql/` must run against a populated store.

    These are hand-written SQL against a schema that changes. Nothing else in
    the suite would notice a renamed column, because the queries are only
    exercised by a human typing `make db-query`.
    """

    QUERIES = (
        "01_execution_summary",
        "02_benchmark_disagreement",
        "03_cost_attribution",
        "04_markout_adverse_selection",
        "05_queue_policy_sensitivity",
        "06_fill_quality_by_liquidity",
        "07_data_quality_summary",
    )

    @pytest.mark.parametrize("name", QUERIES)
    def test_every_query_runs(self, store, name):
        frame = store.run_named(name)
        assert frame is not None
        # A query that returns no columns is a query that silently selected
        # nothing, which is a different failure from returning no rows.
        assert len(frame.columns) > 0, f"{name} returned no columns"

    @pytest.mark.parametrize("name", QUERIES)
    def test_every_query_declares_its_question(self, store, name):
        """The first comment line is the question the query answers.

        A query whose purpose is not stated cannot be checked for whether it
        still answers it.
        """
        sql = store.load_query(name)
        first = next(line for line in sql.splitlines() if line.strip().startswith("--"))
        assert "-- Question:" in first, f"{name} does not open with '-- Question:'"

    def test_the_query_set_is_what_the_tests_cover(self, store):
        """A new .sql file must be added to QUERIES, or it goes untested."""
        assert set(store.available_queries()) == set(self.QUERIES), (
            "sql/ and TestPackagedQueries.QUERIES disagree; add the new query so "
            "it is executed by the suite"
        )

    def test_queries_return_rows_when_the_store_has_data(self, store):
        """The store is populated, so a query returning nothing means the join
        keys or the filters have drifted away from the data."""
        empty = [name for name in self.QUERIES if len(store.run_named(name)) == 0]
        assert not empty, f"these queries returned no rows against a full store: {empty}"


class TestQueryDependencies:
    def test_required_tables_are_detected_from_the_sql(self, store):
        assert set(store.required_tables("07_data_quality_summary")) == {
            "events",
            "experiment_runs",
            "executions",
            "datasets",
        }

    def test_required_tables_ignores_columns_and_aliases(self, store):
        """Only whole-word table names count, not a column that shares a name."""
        required = store.required_tables("01_execution_summary")
        assert "executions" in required
        # `events` appears nowhere in query 01, and a naive substring search
        # would still match it inside a column like `event_count`.
        assert (
            "events" not in required
            or "FROM events" in store.load_query("01_execution_summary").lower()
        )

    def test_missing_tables_for_is_empty_on_a_complete_store(self, store):
        for name in TestPackagedQueries.QUERIES:
            assert store.missing_tables_for(name) == (), (
                f"{name} needs a table the store does not hold"
            )

    def test_missing_tables_for_names_what_is_absent(self, project_root):
        """On an empty store, every dependency is reported as missing."""
        root = STORE_ROOT / f"empty-{uuid.uuid4().hex[:8]}"
        root.mkdir(parents=True, exist_ok=True)
        try:
            store = DuckDbStore(root, sql_dir=project_root / "sql")
            try:
                missing = store.missing_tables_for("07_data_quality_summary")
            finally:
                store.close()
            assert set(missing) == {"events", "experiment_runs", "executions", "datasets"}
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_an_unknown_query_name_raises(self, store):
        from tradeforge.domain.exceptions import ConfigurationError

        with pytest.raises(ConfigurationError, match="unknown query"):
            store.load_query("99_does_not_exist")


class TestQueryContent:
    """Claims the SQL makes in its own comments, checked against the SQL."""

    def test_query_07_counts_dirty_runs(self, store):
        """Its header calls `n_dirty_runs > 0` a reproducibility failure, so the
        column has to exist and be a count."""
        sql = store.load_query("07_data_quality_summary")
        assert "n_dirty_runs" in sql
        assert "git_dirty" in sql
        frame = store.run_named("07_data_quality_summary")
        assert "n_dirty_runs" in frame.columns

    def test_query_07_reports_the_data_tier_and_queue_mode(self, store):
        """The tier and the queue mode are what make the other six interpretable."""
        sql = store.load_query("07_data_quality_summary").lower()
        assert "data_type" in sql
        assert "queue_mode" in sql

    def test_queries_do_not_select_star_from_a_join_without_naming_columns(self, store):
        """`SELECT *` across a join produces duplicate column names, which
        pandas then renames to `.1` suffixes - a silent way for a downstream
        reader to pick the wrong column."""
        for name in TestPackagedQueries.QUERIES:
            sql = store.load_query(name)
            selects = re.findall(r"SELECT\s+\*", sql, flags=re.IGNORECASE)
            joins = re.findall(r"\bJOIN\b", sql, flags=re.IGNORECASE)
            assert not (selects and joins), (
                f"{name} uses SELECT * with a JOIN; name the columns instead"
            )
