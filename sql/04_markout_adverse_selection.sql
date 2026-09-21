-- Question: were our fills adversely selected?
--
-- Positive markout = adverse (the mid moved against us after the fill).
--
-- The shape of this table across horizons is the finding, not any single row.
-- Passive execution typically shows a *negative* short-horizon markout (we
-- earned the spread and the mid had not yet moved) and a *positive* longer
-- horizon markout (we were filled precisely because informed flow was coming).
-- That reversal is adverse selection, and it is invisible in an average fill
-- price.
--
-- `coverage` below 1.0 means some fills' horizon extended past the recorded
-- data. Those fills are excluded rather than imputed as zero: a missing markout
-- and a flat markout are different facts.
--
-- `weighted_median_gap_bps` is the distance between the volume-weighted and the
-- median markout. A large gap means a few large fills dominate the average,
-- which is worth knowing before quoting it.

SELECT
    e.policy,
    e.side,
    m.horizon_ns,
    ROUND(m.horizon_ns / 1000000000.0, 1)           AS horizon_s,
    COUNT(*)                                        AS n_runs,
    ROUND(AVG(m.volume_weighted_bps), 4)            AS mean_weighted_markout_bps,
    ROUND(AVG(m.median_bps), 4)                     AS mean_median_markout_bps,
    ROUND(AVG(m.volume_weighted_bps - m.median_bps), 4) AS weighted_median_gap_bps,
    ROUND(AVG(CAST(m.n_measurable AS DOUBLE)
              / NULLIF(m.n_fills, 0)), 4)           AS mean_coverage,
    SUM(m.n_fills)                                  AS total_fills,
    SUM(m.n_measurable)                             AS total_measurable
FROM markouts m
JOIN executions e
  ON e.run_id = m.run_id AND e.seed = m.seed
GROUP BY e.policy, e.side, m.horizon_ns
ORDER BY e.policy, e.side, m.horizon_ns;
