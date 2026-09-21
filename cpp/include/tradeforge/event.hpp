// The normalized event, mirroring `tradeforge.domain.events.MarketEvent`.
//
// This struct is the wire format between the two implementations. Any field
// added here must be added there in the same change: the differential test
// compares decoded fields one by one, so a drift shows up as a failing test
// rather than as a subtly different backtest.

#pragma once

#include <cstdint>
#include <string>

#include "tradeforge/types.hpp"

namespace tradeforge {

// Bitfield flags, matching `tradeforge.domain.enums.EventFlag`.
inline constexpr std::uint32_t kFlagAggressorBuy = 1U << 0U;
inline constexpr std::uint32_t kFlagAggressorSell = 1U << 1U;
inline constexpr std::uint32_t kFlagAuction = 1U << 2U;
inline constexpr std::uint32_t kFlagIntermarketSweep = 1U << 3U;
inline constexpr std::uint32_t kFlagHidden = 1U << 4U;
inline constexpr std::uint32_t kFlagRepaired = 1U << 5U;

struct Event {
    SequenceId sequence_id{0};
    TimestampNs exchange_timestamp_ns{0};
    TimestampNs receive_timestamp_ns{0};  // 0 means "not provided"
    EventType type{EventType::Add};
    Side side{Side::Buy};
    bool has_side{false};
    PriceTicks price_ticks{kInvalidPrice};
    QuantityBase quantity_base{0};
    OrderId order_id{0};
    bool has_order_id{false};
    std::uint32_t flags{0};
    std::string symbol;

    // Ordering key: venue time, then source sequence. Two events with the same
    // timestamp keep their source order, which is what makes a replay
    // deterministic across machines.
    [[nodiscard]] bool precedes(const Event& other) const noexcept {
        if (exchange_timestamp_ns != other.exchange_timestamp_ns) {
            return exchange_timestamp_ns < other.exchange_timestamp_ns;
        }
        return sequence_id < other.sequence_id;
    }

    // The aggressor side if the source declared one, else nothing. Never
    // inferred from price: inferring is a guess, and a guess in the matching
    // path becomes a fabricated fill.
    [[nodiscard]] bool has_aggressor() const noexcept {
        return (flags & (kFlagAggressorBuy | kFlagAggressorSell)) != 0U;
    }

    [[nodiscard]] Side aggressor_side() const noexcept {
        return (flags & kFlagAggressorBuy) != 0U ? Side::Buy : Side::Sell;
    }
};

}  // namespace tradeforge
