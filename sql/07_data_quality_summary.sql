-- Question: can these results be trusted, and under what conditions were they
-- produced?
--
-- Every other query in this directory is only interpretable in the light of
-- this one. It answers:
--
--   * what data tier produced the results (L1 / L2_MBP / L3_MBO);
--   * whether the queue position was exact or an estimate;
--   * whether latency was a scenario or observed;
--   * how many events were available and how many distinct sessions ran;
--   * whether any run carried a dirty working tree, which means the recorded
--     git commit does not fully describe the code that produced it.
--
-- `n_dirty_runs` greater than zero is a reproducibility failure and should be
-- treated as such before any number from this store is quoted.

WITH event_stats AS (
    SELECT
        dataset_name,
        COUNT(*)            AS n_events,
        COUNT(DISTINCT seed) AS n_seeds,
        MIN(exchange_timestamp_ns) AS first_event_ns,
        MAX(exchange_timestamp_ns) AS last_event_ns,
        SUM(CASE WHEN event_type = 'TRADE' THEN 1 ELSE 0 END) AS n_trades,
        SUM(CASE WHEN event_type = 'ADD' THEN 1 ELSE 0 END)   AS n_adds,
        SUM(CASE WHEN event_type = 'CANCEL' THEN 1 ELSE 0 END) AS n_cancels,
        SUM(CASE WHEN receive_timestamp_ns IS NULL THEN 1 ELSE 0 END) AS n_without_receive_ts
    FROM events
    GROUP BY dataset_name
),
run_stats AS (
    SELECT
        COUNT(*)                        AS n_runs,
        COUNT(DISTINCT seed)            AS n_seed_values,
        COUNT(DISTINCT policy)          AS n_policies,
        COUNT(DISTINCT queue_mode)      AS n_queue_modes,
        COUNT(DISTINCT latency_basis)   AS n_latency_bases,
        COUNT(DISTINCT counterfactual_mode) AS n_counterfactual_modes,
        SUM(CASE WHEN maker_fill_ratio > 0 AND maker_fill_ratio < 1
                 THEN 1 ELSE 0 END)     AS n_mixed_liquidity_runs
    FROM executions
),
provenance AS (
    SELECT
        MIN(data_type)      AS data_type,
        MIN(provenance)     AS provenance,
        MIN(license)        AS license,
        COUNT(DISTINCT config_fingerprint) AS n_config_fingerprints
    FROM datasets
),
experiment_health AS (
    SELECT
        COUNT(*)                                        AS n_experiment_rows,
        SUM(CASE WHEN git_dirty THEN 1 ELSE 0 END)      AS n_dirty_runs,
        COUNT(DISTINCT git_commit)                      AS n_git_commits
    FROM experiment_runs
)
SELECT * FROM event_stats, run_stats, provenance, experiment_health;
