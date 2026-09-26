"""Storage schema.

The table set is deliberately small and normalised around one fact: **a parent
order execution**. Everything else either describes the conditions it ran under
(`datasets`, `experiments`), or decomposes it (`child_orders`, `fills`,
`tca_metrics`, `markouts`).

Column names match the dataclass field names they come from, so a rename in the
domain surfaces as an obvious schema mismatch rather than a silently wrong
query.

**Traceability.** Every table is reachable from a table that carries
`config_fingerprint`, but only three tables carry it themselves:

  * `datasets`, `executions` and `experiment_runs` are roots and hold the
    fingerprint directly;
  * `events` joins to `datasets` on `(dataset_name, seed)`;
  * `child_orders`, `fills`, `tca_metrics` and `markouts` join to `executions`
    on `(run_id, seed)`.

Denormalising the fingerprint onto every child row would repeat one 16-character
string across tens of thousands of fills for no extra traceability. The rule
that matters is that the join key exists, and `tests/unit/test_storage.py`
asserts it rather than asserting a column that was never meant to be there.

An earlier version of this docstring claimed "every table carries
`config_fingerprint`", which was false for five of the eight.
"""

from __future__ import annotations

TABLE_DDL: dict[str, str] = {
    "datasets": """
        CREATE TABLE IF NOT EXISTS datasets (
            dataset_name        VARCHAR NOT NULL,
            dataset_version     VARCHAR NOT NULL,
            data_type           VARCHAR NOT NULL,
            provenance          VARCHAR NOT NULL,
            redistributable     BOOLEAN NOT NULL,
            license             VARCHAR,
            config_fingerprint  VARCHAR NOT NULL,
            ingested_at_utc     VARCHAR NOT NULL,
            PRIMARY KEY (dataset_name, dataset_version, config_fingerprint)
        )
    """,
    "events": """
        CREATE TABLE IF NOT EXISTS events (
            dataset_name        VARCHAR NOT NULL,
            seed                INTEGER NOT NULL,
            sequence_id         BIGINT  NOT NULL,
            exchange_timestamp_ns BIGINT NOT NULL,
            receive_timestamp_ns  BIGINT,
            symbol              VARCHAR NOT NULL,
            event_type          VARCHAR NOT NULL,
            side                VARCHAR,
            price_ticks         BIGINT,
            quantity_base       BIGINT,
            order_id            BIGINT,
            trade_id            BIGINT,
            flags               INTEGER NOT NULL,
            source              VARCHAR NOT NULL,
            PRIMARY KEY (dataset_name, seed, sequence_id)
        )
    """,
    "executions": """
        CREATE TABLE IF NOT EXISTS executions (
            run_id              VARCHAR NOT NULL,
            parent_order_id     VARCHAR NOT NULL,
            symbol              VARCHAR NOT NULL,
            side                VARCHAR NOT NULL,
            policy              VARCHAR NOT NULL,
            requested_base      BIGINT  NOT NULL,
            filled_base         BIGINT  NOT NULL,
            unfilled_base       BIGINT  NOT NULL,
            completion_rate     DOUBLE  NOT NULL,
            start_ns            BIGINT  NOT NULL,
            end_ns              BIGINT  NOT NULL,
            duration_ns         BIGINT  NOT NULL,
            arrival_mid_ticks   DOUBLE,
            terminal_mid_ticks  DOUBLE,
            avg_fill_price_ticks DOUBLE,
            notional            DOUBLE  NOT NULL,
            fees_total          DOUBLE  NOT NULL,
            maker_filled_base   BIGINT  NOT NULL,
            taker_filled_base   BIGINT  NOT NULL,
            maker_fill_ratio    DOUBLE  NOT NULL,
            market_volume_base  BIGINT  NOT NULL,
            participation_rate  DOUBLE  NOT NULL,
            n_child_orders      INTEGER NOT NULL,
            n_rejects           INTEGER NOT NULL,
            n_cancels           INTEGER NOT NULL,
            queue_mode          VARCHAR NOT NULL,
            latency_basis       VARCHAR NOT NULL,
            counterfactual_mode VARCHAR NOT NULL,
            seed                INTEGER NOT NULL,
            config_fingerprint  VARCHAR NOT NULL,
            PRIMARY KEY (run_id, seed)
        )
    """,
    "child_orders": """
        CREATE TABLE IF NOT EXISTS child_orders (
            run_id              VARCHAR NOT NULL,
            seed                INTEGER NOT NULL,
            client_order_id     VARCHAR NOT NULL,
            parent_order_id     VARCHAR NOT NULL,
            slice_index         INTEGER NOT NULL,
            side                VARCHAR NOT NULL,
            quantity_base       BIGINT  NOT NULL,
            filled_base         BIGINT  NOT NULL,
            status              VARCHAR NOT NULL,
            style               VARCHAR NOT NULL,
            order_type          VARCHAR NOT NULL,
            time_in_force       VARCHAR NOT NULL,
            placed_price_ticks  BIGINT,
            estimated_queue_ahead_base BIGINT,
            created_ns          BIGINT  NOT NULL,
            working_ns          BIGINT,
            terminal_ns         BIGINT,
            PRIMARY KEY (run_id, seed, client_order_id)
        )
    """,
    "fills": """
        CREATE TABLE IF NOT EXISTS fills (
            run_id              VARCHAR NOT NULL,
            seed                INTEGER NOT NULL,
            fill_id             BIGINT  NOT NULL,
            client_order_id     VARCHAR NOT NULL,
            parent_order_id     VARCHAR NOT NULL,
            symbol              VARCHAR NOT NULL,
            side                VARCHAR NOT NULL,
            price_ticks         BIGINT  NOT NULL,
            quantity_base       BIGINT  NOT NULL,
            timestamp_ns        BIGINT  NOT NULL,
            liquidity_flag      VARCHAR NOT NULL,
            fee                 DOUBLE  NOT NULL,
            queue_wait_ns       BIGINT  NOT NULL,
            sequence_id         BIGINT  NOT NULL,
            PRIMARY KEY (run_id, seed, fill_id)
        )
    """,
    "tca_metrics": """
        CREATE TABLE IF NOT EXISTS tca_metrics (
            run_id              VARCHAR NOT NULL,
            seed                INTEGER NOT NULL,
            policy              VARCHAR NOT NULL,
            side                VARCHAR NOT NULL,
            fill_ratio          DOUBLE NOT NULL,
            arrival_mid_ticks   DOUBLE,
            interval_vwap_ticks DOUBLE,
            interval_twap_ticks DOUBLE,
            interval_mid_ticks  DOUBLE,
            terminal_mid_ticks  DOUBLE,
            cost_vs_arrival_bps DOUBLE,
            cost_vs_vwap_bps    DOUBLE,
            cost_vs_twap_bps    DOUBLE,
            cost_vs_interval_mid_bps DOUBLE,
            cost_vs_terminal_bps DOUBLE,
            implementation_shortfall_bps DOUBLE,
            spread_cost_bps     DOUBLE,
            fees_bps            DOUBLE,
            timing_bps          DOUBLE,
            residual_impact_bps DOUBLE,
            opportunity_cost_bps DOUBLE,
            n_mid_observations  INTEGER NOT NULL,
            n_trade_prints      INTEGER NOT NULL,
            window_volume_base  BIGINT NOT NULL,
            PRIMARY KEY (run_id, seed)
        )
    """,
    "markouts": """
        CREATE TABLE IF NOT EXISTS markouts (
            run_id              VARCHAR NOT NULL,
            seed                INTEGER NOT NULL,
            horizon_ns          BIGINT NOT NULL,
            n_fills             INTEGER NOT NULL,
            n_measurable        INTEGER NOT NULL,
            volume_weighted_bps DOUBLE,
            mean_bps            DOUBLE,
            median_bps          DOUBLE,
            p25_bps             DOUBLE,
            p75_bps             DOUBLE,
            worst_bps           DOUBLE,
            best_bps            DOUBLE,
            PRIMARY KEY (run_id, seed, horizon_ns)
        )
    """,
    "experiment_runs": """
        CREATE TABLE IF NOT EXISTS experiment_runs (
            experiment          VARCHAR NOT NULL,
            cell                VARCHAR NOT NULL,
            seed                INTEGER NOT NULL,
            metric              VARCHAR NOT NULL,
            metric_value        DOUBLE,
            config_fingerprint  VARCHAR NOT NULL,
            git_commit          VARCHAR NOT NULL,
            git_dirty           BOOLEAN NOT NULL,
            recorded_at_utc     VARCHAR NOT NULL,
            tags_json           VARCHAR NOT NULL,
            PRIMARY KEY (experiment, cell, seed, metric)
        )
    """,
}

TABLE_ORDER: tuple[str, ...] = (
    "datasets",
    "events",
    "executions",
    "child_orders",
    "fills",
    "tca_metrics",
    "markouts",
    "experiment_runs",
)
