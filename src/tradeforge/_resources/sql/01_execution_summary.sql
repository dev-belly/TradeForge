-- Question: for each execution algorithm, what did execution cost and how
-- completely did it fill?
--
-- Reads `executions` joined to `tca_metrics`. The join is on (run_id, seed)
-- because a run is only meaningful together with the session it ran on: the
-- same policy on two seeds is two observations, not one.
--
-- Note the deliberate absence of a "winner" column. Ranking algorithms on a
-- mean is exactly the p-hacking this platform exists to avoid; the paired,
-- bootstrapped comparison lives in the Python research layer, where the
-- interval and the correction are attached to the number.

SELECT
    e.policy,
    e.side,
    COUNT(*)                                    AS n_runs,
    ROUND(AVG(e.completion_rate), 4)            AS mean_fill_ratio,
    ROUND(AVG(e.participation_rate), 6)         AS mean_participation,
    ROUND(AVG(e.maker_fill_ratio), 4)           AS mean_maker_ratio,
    ROUND(AVG(t.cost_vs_arrival_bps), 3)        AS mean_cost_vs_arrival_bps,
    ROUND(AVG(t.cost_vs_vwap_bps), 3)           AS mean_cost_vs_vwap_bps,
    ROUND(AVG(t.cost_vs_twap_bps), 3)           AS mean_cost_vs_twap_bps,
    ROUND(AVG(t.implementation_shortfall_bps), 3) AS mean_is_bps,
    ROUND(AVG(t.fees_bps), 3)                   AS mean_fees_bps,
    MIN(e.queue_mode)                           AS queue_mode,
    MIN(e.latency_basis)                        AS latency_basis,
    MIN(e.counterfactual_mode)                  AS counterfactual_mode
FROM executions e
JOIN tca_metrics t
  ON t.run_id = e.run_id AND t.seed = e.seed
GROUP BY e.policy, e.side
ORDER BY mean_is_bps;
