-- Question: what did maker fills and taker fills actually cost us?
--
-- The two liquidity types are economically different and must never be pooled:
-- a taker fill pays the spread, a maker fill earns it, and the maker fill also
-- carries adverse selection that only shows up in the markouts.
--
-- `queue_wait_ns` is reported for maker fills only. It is the time between our
-- order joining the level and the trade that consumed it, which is the direct
-- observable of the queue model's behaviour: if the estimated queue ahead is
-- wrong, this distribution is where it shows up.
--
-- The fee column is signed. A negative `mean_fee_per_share` on maker fills is a
-- rebate, and it is the only reason a passive strategy can look cheap while
-- being adversely selected.

SELECT
    f.side,
    f.liquidity_flag,
    COUNT(*)                                            AS n_fills,
    SUM(f.quantity_base)                                AS filled_base,
    ROUND(AVG(f.price_ticks), 4)                        AS mean_price_ticks,
    ROUND(SUM(f.fee) / NULLIF(SUM(f.quantity_base), 0), 8) AS mean_fee_per_share,
    ROUND(AVG(f.fee), 6)                                AS mean_fee_per_fill,
    ROUND(AVG(CASE WHEN f.liquidity_flag = 'MAKER'
                   THEN f.queue_wait_ns / 1000000000.0 END), 4) AS mean_queue_wait_s,
    ROUND(MAX(CASE WHEN f.liquidity_flag = 'MAKER'
                   THEN f.queue_wait_ns / 1000000000.0 END), 4) AS max_queue_wait_s
FROM fills f
GROUP BY f.side, f.liquidity_flag
ORDER BY f.side, f.liquidity_flag;
