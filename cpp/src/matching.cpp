#include "tradeforge/matching.hpp"

#include <algorithm>

namespace tradeforge {
namespace {

constexpr std::size_t kUnlimited = 0;

// A limit order is acceptable at a level only if the level is at or better than
// the limit. Integer comparison throughout: no tolerance, no epsilon.
[[nodiscard]] bool price_acceptable(Side side, PriceTicks level_price,
                                    std::optional<PriceTicks> limit) noexcept {
    if (!limit.has_value()) {
        return true;
    }
    return side == Side::Buy ? level_price <= *limit : level_price >= *limit;
}

}  // namespace

std::optional<double> MatchResult::average_price_ticks() const noexcept {
    if (filled_base <= 0) {
        return std::nullopt;
    }
    // Accumulated in int64 ticks first, divided once at the end. Summing in
    // double would lose exactness for no benefit.
    std::int64_t notional_ticks = 0;
    for (const MatchFill& fill : fills) {
        notional_ticks += fill.price_ticks * fill.quantity_base;
    }
    return static_cast<double>(notional_ticks) / static_cast<double>(filled_base);
}

MatchResult match_order(const Snapshot& snapshot, Side side, QuantityBase quantity_base,
                        std::optional<PriceTicks> limit_price_ticks, TimeInForce tif,
                        std::size_t max_levels_crossed) {
    if (quantity_base <= 0) {
        throw IntegrityError("order quantity must be positive, got " +
                             std::to_string(quantity_base));
    }

    MatchResult result;
    result.requested_base = quantity_base;

    const std::vector<Level>& book = side == Side::Buy ? snapshot.asks : snapshot.bids;
    if (book.empty()) {
        result.remaining_base = quantity_base;
        return result;
    }

    QuantityBase remaining = quantity_base;
    for (const Level& level : book) {
        if (remaining <= 0) {
            break;
        }
        if (max_levels_crossed != kUnlimited && result.fills.size() >= max_levels_crossed) {
            break;
        }
        if (!price_acceptable(side, level.price_ticks, limit_price_ticks)) {
            break;
        }
        const QuantityBase take = std::min(level.quantity_base, remaining);
        if (take <= 0) {
            continue;
        }
        result.fills.push_back(MatchFill{level.price_ticks, take});
        remaining -= take;
    }

    result.filled_base = quantity_base - remaining;
    result.levels_crossed = result.fills.size();

    if (tif == TimeInForce::Fok && remaining > 0) {
        // All-or-nothing: discard whatever was crossed.
        result.fills.clear();
        result.filled_base = 0;
        result.levels_crossed = 0;
        result.remaining_base = quantity_base;
        return result;
    }
    if (tif == TimeInForce::Ioc && remaining > 0) {
        // IOC keeps the crossed part and cancels the rest, so it reports no
        // remainder: the unfilled quantity is gone, not resting.
        result.remaining_base = 0;
        return result;
    }
    result.remaining_base = remaining;
    return result;
}

bool is_marketable(const Snapshot& snapshot, Side side,
                   std::optional<PriceTicks> price_ticks) noexcept {
    if (!price_ticks.has_value()) {
        return true;  // a market order
    }
    if (side == Side::Buy) {
        const auto ask = snapshot.best_ask();
        return ask.has_value() && *price_ticks >= *ask;
    }
    const auto bid = snapshot.best_bid();
    return bid.has_value() && *price_ticks <= *bid;
}

std::optional<PriceTicks> resting_price_ticks(const Snapshot& snapshot, Side side,
                                              PriceTicks offset_ticks) noexcept {
    if (side == Side::Buy) {
        const auto bid = snapshot.best_bid();
        if (!bid.has_value()) {
            return std::nullopt;
        }
        return *bid - offset_ticks;
    }
    const auto ask = snapshot.best_ask();
    if (!ask.has_value()) {
        return std::nullopt;
    }
    return *ask + offset_ticks;
}

}  // namespace tradeforge
