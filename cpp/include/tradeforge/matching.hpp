// Simulated matching against an observable snapshot.
//
// This is NOT the replay path. Replay reconstructs what happened; this answers
// "what would happen to our order if it arrived now". Keeping the two as
// separate functions with separate names is deliberate: the classic bug is a
// backtest that silently mixes them and then reports our fills as market data.
//
// Documented limitation, mirrored in the Python implementation: simulated fills
// do not remove liquidity from the replayed book, because our orders were not in
// the historical stream.

#pragma once

#include <cstddef>
#include <optional>
#include <vector>

#include "tradeforge/book.hpp"
#include "tradeforge/types.hpp"

namespace tradeforge {

enum class TimeInForce : std::uint8_t { Day = 0, Gtc = 1, Ioc = 2, Fok = 3 };

struct MatchFill {
    PriceTicks price_ticks{0};
    QuantityBase quantity_base{0};
};

struct MatchResult {
    std::vector<MatchFill> fills;
    QuantityBase requested_base{0};
    QuantityBase filled_base{0};
    QuantityBase remaining_base{0};
    std::size_t levels_crossed{0};

    [[nodiscard]] bool is_empty() const noexcept { return fills.empty(); }

    // Volume-weighted average, in ticks. Returns nullopt rather than 0.0 for an
    // empty result: "no fills" and "filled at price zero" are different facts.
    [[nodiscard]] std::optional<double> average_price_ticks() const noexcept;
};

// Cross the book with `quantity_base`.
//
// Price-time priority is implicit in walking levels from the touch outwards.
// FIFO *within* a level is not observable from aggregated data, which is exactly
// why passive fills go through the queue model instead of this function.
[[nodiscard]] MatchResult match_order(const Snapshot& snapshot, Side side,
                                      QuantityBase quantity_base,
                                      std::optional<PriceTicks> limit_price_ticks,
                                      TimeInForce tif = TimeInForce::Day,
                                      std::size_t max_levels_crossed = 0);

// Whether a limit order priced at `price_ticks` is marketable against `snapshot`.
[[nodiscard]] bool is_marketable(const Snapshot& snapshot, Side side,
                                 std::optional<PriceTicks> price_ticks) noexcept;

// Where a PASSIVE order of `side` joins the queue. A buy joins the bid side,
// not the ask: getting this backwards posts liquidity on the wrong side of the
// market and produces fills that could not have happened.
[[nodiscard]] std::optional<PriceTicks> resting_price_ticks(const Snapshot& snapshot, Side side,
                                                            PriceTicks offset_ticks = 0) noexcept;

}  // namespace tradeforge
