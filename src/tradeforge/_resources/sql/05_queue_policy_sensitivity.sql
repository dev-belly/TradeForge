-- Question: how much does the L2 queue assumption actually move the answer?
--
-- With aggregated level data the queue position is unobservable, so the only
-- honest treatment is to vary the assumption and report the spread. The three
-- policies differ in how unexplained level shrinkage is attributed:
--
--   optimistic   - removed shares were BEHIND us (we move up the queue)
--   neutral      - removed shares split in proportion to our visible share
--   conservative - removed shares were AHEAD of us (we move back)
--
-- `policy_spread_bps` is max minus min across the three. That number is the
-- honest error bar on any passive-execution claim made from L2 data. If it is
-- larger than the difference between two algorithms, then the algorithms are
-- not distinguishable at this data tier - no matter what the point estimates
-- say.
--
-- Runs where the queue mode was EXACT are excluded: those do not rest on the
-- assumption and would flatten the spread.

WITH per_policy AS (
    SELECT
        e.policy,
        e.seed,
        e.config_fingerprint,
        AVG(t.implementation_shortfall_bps) AS is_bps
    FROM executions e
    JOIN tca_metrics t
      ON t.run_id = e.run_id AND t.seed = e.seed
    WHERE e.queue_mode = 'APPROXIMATE'
    GROUP BY e.policy, e.seed, e.config_fingerprint
)
SELECT
    policy,
    COUNT(*)                                    AS n_observations,
    ROUND(MIN(is_bps), 3)                       AS min_is_bps,
    ROUND(MAX(is_bps), 3)                       AS max_is_bps,
    ROUND(MAX(is_bps) - MIN(is_bps), 3)         AS policy_spread_bps,
    ROUND(AVG(is_bps), 3)                       AS mean_is_bps,
    'queue position is an estimate from aggregated data' AS caveat
FROM per_policy
GROUP BY policy
ORDER BY policy_spread_bps DESC;
