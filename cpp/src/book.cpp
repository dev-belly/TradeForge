#include "tradeforge/book.hpp"

#include <string>
#include <utility>

namespace tradeforge {
namespace {

std::string side_name(Side side) { return to_string(side); }

}  // namespace

MbpBook::MbpBook(std::string symbol, std::size_t max_levels_per_side, bool strict_crossed)
    : symbol_(std::move(symbol)),
      max_levels_per_side_(max_levels_per_side),
      strict_crossed_(strict_crossed) {}

void MbpBook::apply(const Event& event) {
    if (!symbol_.empty() && !event.symbol.empty() && event.symbol != symbol_) {
        throw IntegrityError("symbol mismatch: book=" + symbol_ + " event=" + event.symbol);
    }
    if (!mutates_book(event.type)) {
        last_sequence_id_ = event.sequence_id;
        last_timestamp_ns_ = event.exchange_timestamp_ns;
        return;
    }

    last_sequence_id_ = event.sequence_id;
    last_timestamp_ns_ = event.exchange_timestamp_ns;
    ++stats_.updates;

    if (event.type == EventType::Clear) {
        clear();
        return;
    }

    if (event.price_ticks == kInvalidPrice && event.type != EventType::Clear) {
        throw IntegrityError("event missing price_ticks (seq=" +
                             std::to_string(event.sequence_id) + ")");
    }
    if (event.quantity_base <= 0) {
        throw IntegrityError("non-positive quantity " + std::to_string(event.quantity_base) +
                             " (seq=" + std::to_string(event.sequence_id) + ")");
    }

    switch (event.type) {
        case EventType::Add:
            apply_add(event);
            break;
        case EventType::Cancel:
            apply_cancel(event);
            break;
        case EventType::Snapshot:
            apply_snapshot_level(event);
            break;
        case EventType::Trade:
            apply_trade(event);
            break;
        case EventType::Modify:
        case EventType::Replace:
            // Aggregated data has no order identity, so a modify cannot be
            // interpreted. Guessing would corrupt the book silently.
            throw IntegrityError("MODIFY requires order identity; the MBP book cannot "
                                 "interpret it. Emit CANCEL + ADD instead.");
        case EventType::Clear:
        case EventType::Halt:
        case EventType::Resume:
            break;
    }

    check_invariants(event);
}

void MbpBook::apply_add(const Event& event) {
    if (!event.has_side) {
        throw IntegrityError("ADD requires a side (seq=" + std::to_string(event.sequence_id) + ")");
    }
    if (side_levels(event.side) >= max_levels_per_side_ &&
        level_size(event.side, event.price_ticks) == 0) {
        throw IntegrityError("level limit " + std::to_string(max_levels_per_side_) +
                             " exceeded on " + side_name(event.side) + " side");
    }
    if (event.side == Side::Buy) {
        bids_[event.price_ticks] += event.quantity_base;
    } else {
        asks_[event.price_ticks] += event.quantity_base;
    }
}

void MbpBook::apply_cancel(const Event& event) {
    if (!event.has_side) {
        throw IntegrityError("CANCEL requires a side (seq=" +
                             std::to_string(event.sequence_id) + ")");
    }
    // The removed quantity equals the requested quantity by construction here;
    // the return value matters only where a partial removal is possible.
    static_cast<void>(remove_at(event.side, event.price_ticks, event.quantity_base));
}

void MbpBook::apply_snapshot_level(const Event& event) {
    if (!event.has_side) {
        throw IntegrityError("SNAPSHOT requires a side (seq=" +
                             std::to_string(event.sequence_id) + ")");
    }
    if (event.side == Side::Buy) {
        bids_[event.price_ticks] = event.quantity_base;
    } else {
        asks_[event.price_ticks] = event.quantity_base;
    }
}

void MbpBook::apply_trade(const Event& event) {
    ++stats_.trades;
    stats_.traded_volume_base += event.quantity_base;

    Side aggressor{};
    if (event.has_aggressor()) {
        aggressor = event.aggressor_side();
    } else if (event.has_side) {
        aggressor = event.side;
    } else {
        // An unattributed print cannot say which side was consumed. Skipping it
        // is honest; guessing would remove depth from an arbitrary side.
        ++stats_.skipped_unattributed_trades;
        return;
    }

    const Side resting = opposite(aggressor);
    const QuantityBase available = level_size(resting, event.price_ticks);
    if (available < event.quantity_base) {
        throw IntegrityError("trade of " + std::to_string(event.quantity_base) + " at " +
                             std::to_string(event.price_ticks) +
                             " exceeds reconstructed depth " + std::to_string(available) +
                             " on " + side_name(resting) + " side");
    }
    static_cast<void>(remove_at(resting, event.price_ticks, event.quantity_base));
}

QuantityBase MbpBook::remove_at(Side side, PriceTicks price, QuantityBase quantity) {
    if (quantity == 0) {
        return 0;
    }
    if (side == Side::Buy) {
        auto it = bids_.find(price);
        if (it == bids_.end()) {
            throw IntegrityError("remove at empty price " + std::to_string(price) + " on bid side");
        }
        if (it->second < quantity) {
            throw IntegrityError("negative depth at price " + std::to_string(price) +
                                 ": have " + std::to_string(it->second) + ", remove " +
                                 std::to_string(quantity));
        }
        it->second -= quantity;
        if (it->second == 0) {
            bids_.erase(it);
        }
    } else {
        auto it = asks_.find(price);
        if (it == asks_.end()) {
            throw IntegrityError("remove at empty price " + std::to_string(price) + " on ask side");
        }
        if (it->second < quantity) {
            throw IntegrityError("negative depth at price " + std::to_string(price) +
                                 ": have " + std::to_string(it->second) + ", remove " +
                                 std::to_string(quantity));
        }
        it->second -= quantity;
        if (it->second == 0) {
            asks_.erase(it);
        }
    }
    return quantity;
}

void MbpBook::check_invariants(const Event& event) {
    if (bids_.empty() || asks_.empty()) {
        return;
    }
    const PriceTicks bid = bids_.begin()->first;
    const PriceTicks ask = asks_.begin()->first;
    if (bid > ask) {
        ++stats_.crossed_attempts;
        if (strict_crossed_) {
            throw IntegrityError("crossed_book: best bid " + std::to_string(bid) +
                                 " > best ask " + std::to_string(ask));
        }
    } else if (bid == ask) {
        // Locked markets occur on real venues. Tolerated, counted, never
        // silently "fixed" by deleting a level.
        ++stats_.locked_attempts;
    }
    (void)event;
}

Snapshot MbpBook::snapshot(std::size_t depth) const {
    Snapshot out;
    out.timestamp_ns = last_timestamp_ns_;
    out.sequence_id = last_sequence_id_;
    out.bids.reserve(depth);
    out.asks.reserve(depth);
    for (const auto& [price, quantity] : bids_) {
        if (out.bids.size() >= depth) {
            break;
        }
        out.bids.push_back(Level{price, quantity, 0});
    }
    for (const auto& [price, quantity] : asks_) {
        if (out.asks.size() >= depth) {
            break;
        }
        out.asks.push_back(Level{price, quantity, 0});
    }
    return out;
}

QuantityBase MbpBook::level_size(Side side, PriceTicks price) const noexcept {
    if (side == Side::Buy) {
        const auto it = bids_.find(price);
        return it == bids_.end() ? 0 : it->second;
    }
    const auto it = asks_.find(price);
    return it == asks_.end() ? 0 : it->second;
}

std::size_t MbpBook::side_levels(Side side) const noexcept {
    return side == Side::Buy ? bids_.size() : asks_.size();
}

void MbpBook::clear() noexcept {
    bids_.clear();
    asks_.clear();
}

}  // namespace tradeforge
