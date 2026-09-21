// Market-by-price (L2) book reconstruction.
//
// This is the production core (ADR-002). The Python `MbpBook` is the reference
// oracle: it is the specification this class must agree with, and the
// differential test asserts that agreement event by event.
//
// Representation: one sorted container per side, keyed by integer ticks. A
// `std::map<PriceTicks, QuantityBase>` with the appropriate comparator gives
// O(log n) insert/erase and O(1) best-price access via `begin()`, which is what
// the replay loop actually needs. A dense tick-indexed array would be faster
// still but requires knowing the band up front; the band is enforced, not
// assumed, so the map is the honest default.
//
// Invariants enforced here, not merely documented:
//   * cancels at a price with no depth raise;
//   * a cancel larger than the displayed depth raises;
//   * a trade against a price with no depth raises;
//   * the book never becomes crossed without raising (locked is tolerated).

#pragma once

#include <cstddef>
#include <map>
#include <optional>
#include <vector>

#include "tradeforge/event.hpp"
#include "tradeforge/types.hpp"

namespace tradeforge {

struct Level {
    PriceTicks price_ticks{0};
    QuantityBase quantity_base{0};
    std::size_t order_count{0};  // always 0 for MBP: the source has no identity
};

struct Snapshot {
    TimestampNs timestamp_ns{0};
    SequenceId sequence_id{-1};
    std::vector<Level> bids;  // best (highest) first
    std::vector<Level> asks;  // best (lowest) first

    [[nodiscard]] std::optional<PriceTicks> best_bid() const noexcept {
        return bids.empty() ? std::nullopt : std::optional<PriceTicks>{bids.front().price_ticks};
    }
    [[nodiscard]] std::optional<PriceTicks> best_ask() const noexcept {
        return asks.empty() ? std::nullopt : std::optional<PriceTicks>{asks.front().price_ticks};
    }
};

// Statistics the replay loop reports. Counters only: no derived metric is
// computed here, because metrics belong to the analysis layer where they can be
// tested in isolation.
struct BookStats {
    std::size_t updates{0};
    std::size_t trades{0};
    QuantityBase traded_volume_base{0};
    std::size_t skipped_unattributed_trades{0};
    std::size_t crossed_attempts{0};
    std::size_t locked_attempts{0};
};

class MbpBook {
public:
    explicit MbpBook(std::string symbol, std::size_t max_levels_per_side = 500,
                     bool strict_crossed = true);

    // Apply one event. Throws IntegrityError on a violated invariant.
    void apply(const Event& event);

    [[nodiscard]] Snapshot snapshot(std::size_t depth) const;
    [[nodiscard]] QuantityBase level_size(Side side, PriceTicks price) const noexcept;
    [[nodiscard]] const std::string& symbol() const noexcept { return symbol_; }
    [[nodiscard]] SequenceId last_sequence_id() const noexcept { return last_sequence_id_; }
    [[nodiscard]] TimestampNs last_timestamp_ns() const noexcept { return last_timestamp_ns_; }
    [[nodiscard]] const BookStats& stats() const noexcept { return stats_; }

    void clear() noexcept;

private:
    // Bids descend from the best price, asks ascend: both maps are "best first"
    // so `begin()` is always the top of book on either side.
    using BidMap = std::map<PriceTicks, QuantityBase, std::greater<PriceTicks>>;
    using AskMap = std::map<PriceTicks, QuantityBase, std::less<PriceTicks>>;

    void apply_add(const Event& event);
    void apply_cancel(const Event& event);
    void apply_snapshot_level(const Event& event);
    void apply_trade(const Event& event);
    void check_invariants(const Event& event);

    [[nodiscard]] QuantityBase remove_at(Side side, PriceTicks price, QuantityBase quantity);
    [[nodiscard]] std::size_t side_levels(Side side) const noexcept;

    std::string symbol_;
    BidMap bids_;
    AskMap asks_;
    std::size_t max_levels_per_side_;
    bool strict_crossed_;
    SequenceId last_sequence_id_{-1};
    TimestampNs last_timestamp_ns_{0};
    BookStats stats_{};
};

}  // namespace tradeforge
