-- Question: where did the basis points actually go?
--
-- The decomposition is additive by construction:
--
--     is_filled_bps = spread_cost_bps + fees_bps + timing_bps + residual_impact_bps
--
-- `residual_impact_bps` is labelled "unexplained" on purpose. It absorbs the
-- denominator approximation between mid-at-fill and arrival, plus any genuine
-- market impact - which this platform does NOT claim to identify, because in a
-- replay our orders never altered the tape.
--
-- `residual_share_of_is` is reported so a large unexplained share is visible
-- rather than hidden behind four tidy columns. If it exceeds roughly 0.5, the
-- decomposition is not describing the execution and should not be quoted.
--
-- `fees_bps` is negative when maker rebates exceed fees.

SELECT
    t.policy,
    t.side,
    COUNT(*)                                        AS n_runs,
    ROUND(AVG(t.implementation_shortfall_bps), 3)   AS is_bps,
    ROUND(AVG(t.spread_cost_bps), 3)                AS spread_cost_bps,
    ROUND(AVG(t.fees_bps), 3)                       AS fees_bps,
    ROUND(AVG(t.timing_bps), 3)                     AS timing_bps,
    ROUND(AVG(t.residual_impact_bps), 3)            AS residual_impact_bps,
    ROUND(AVG(t.opportunity_cost_bps), 3)           AS opportunity_cost_bps,
    ROUND(
        CASE WHEN AVG(t.implementation_shortfall_bps) = 0 THEN NULL
             ELSE AVG(t.residual_impact_bps) / AVG(t.implementation_shortfall_bps)
        END, 4)                                     AS residual_share_of_is
FROM tca_metrics t
GROUP BY t.policy, t.side
ORDER BY residual_share_of_is DESC NULLS LAST;
