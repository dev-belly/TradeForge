-- Question: how much does the choice of benchmark change the story?
--
-- This is the most important query in the set. A strategy that looks excellent
-- against the arrival price can look poor against the interval VWAP, because
-- the arrival benchmark silently credits the strategy with the whole market
-- drift that happened while it was working the order.
--
-- `arrival_minus_vwap_bps` is the size of that illusion. When it is large, any
-- claim of the form "we beat the arrival price by X bps" is mostly a claim
-- about which way the market moved, not about execution quality.
--
-- Only rows where both benchmarks were measurable are included: comparing a
-- benchmark against NULL would produce a flattering gap for free.

SELECT
    t.policy,
    t.side,
    COUNT(*)                                              AS n_runs,
    ROUND(AVG(t.cost_vs_arrival_bps), 3)                  AS mean_vs_arrival_bps,
    ROUND(AVG(t.cost_vs_vwap_bps), 3)                     AS mean_vs_vwap_bps,
    ROUND(AVG(t.cost_vs_twap_bps), 3)                     AS mean_vs_twap_bps,
    ROUND(AVG(t.cost_vs_arrival_bps)
        - AVG(t.cost_vs_vwap_bps), 3)                     AS arrival_minus_vwap_bps,
    ROUND(AVG(t.terminal_mid_ticks - t.arrival_mid_ticks), 3) AS mean_mid_drift_ticks,
    MIN(t.n_mid_observations)                             AS min_mid_observations,
    MIN(t.n_trade_prints)                                 AS min_trade_prints
FROM tca_metrics t
WHERE t.cost_vs_arrival_bps IS NOT NULL
  AND t.cost_vs_vwap_bps IS NOT NULL
GROUP BY t.policy, t.side
ORDER BY ABS(arrival_minus_vwap_bps) DESC;
