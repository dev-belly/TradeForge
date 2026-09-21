// Core scalar types and the price representation.
//
// ADR-001: prices are int64 ticks everywhere in the core. There is no `double`
// price in this header, and there is no implicit conversion to one. Comparing
// floating-point prices for equality is the single most common way a matching
// engine produces impossible fills, so the type system is used to make it
// impossible rather than relying on discipline.
//
// Quantities are int64 base units. Money is never represented as a float: the
// fee and notional paths that need exactness stay on the Python side, which
// owns `Decimal`.

#pragma once

#include <cstdint>
#include <functional>
#include <limits>
#include <stdexcept>
#include <string>

namespace tradeforge {

using PriceTicks = std::int64_t;
using QuantityBase = std::int64_t;
using TimestampNs = std::int64_t;
using SequenceId = std::int64_t;
using OrderId = std::int64_t;

inline constexpr PriceTicks kInvalidPrice = std::numeric_limits<PriceTicks>::min();

enum class Side : std::uint8_t { Buy = 0, Sell = 1 };

[[nodiscard]] constexpr Side opposite(Side side) noexcept {
    return side == Side::Buy ? Side::Sell : Side::Buy;
}

// +1 for buy, -1 for sell. The single place the sign convention lives.
[[nodiscard]] constexpr int sign(Side side) noexcept {
    return side == Side::Buy ? 1 : -1;
}

[[nodiscard]] constexpr const char* to_string(Side side) noexcept {
    return side == Side::Buy ? "BUY" : "SELL";
}

enum class EventType : std::uint8_t {
    Add = 0,
    Cancel = 1,
    Modify = 2,
    Replace = 3,
    Trade = 4,
    Clear = 5,
    Snapshot = 6,
    Halt = 7,
    Resume = 8,
};

// Only these mutate the book. Kept as a free function rather than a member so
// the switch is exhaustive and the compiler warns if a case is added.
[[nodiscard]] constexpr bool mutates_book(EventType type) noexcept {
    switch (type) {
        case EventType::Add:
        case EventType::Cancel:
        case EventType::Modify:
        case EventType::Replace:
        case EventType::Trade:
        case EventType::Clear:
        case EventType::Snapshot:
            return true;
        case EventType::Halt:
        case EventType::Resume:
            return false;
    }
    return false;
}

// Thrown for any violated invariant. The Python binding translates this into
// BookIntegrityError, so the two implementations fail identically.
class IntegrityError : public std::runtime_error {
public:
    explicit IntegrityError(const std::string& message)
        : std::runtime_error(message) {}
};

// Thrown when a capability the declared data tier cannot support is requested.
class CapabilityError : public std::runtime_error {
public:
    explicit CapabilityError(const std::string& message)
        : std::runtime_error(message) {}
};

}  // namespace tradeforge
