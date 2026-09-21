// pybind11 facade. Deliberately narrow.
//
// The binding exposes the book, the matching function and a replay loop -
// nothing else. It does not expose internal containers, it does not accept
// Python callbacks per event (that would make the C++ core's speed depend on
// Python), and it does not translate domain objects beyond the fields listed
// here. A wide binding is how two implementations drift apart.
//
// Exceptions are translated to the same Python types the reference
// implementation raises, so a caller cannot tell which backend produced an
// error - only which backend produced the result.

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <cstdint>
#include <string>
#include <vector>

#include "tradeforge/book.hpp"
#include "tradeforge/event.hpp"
#include "tradeforge/matching.hpp"
#include "tradeforge/types.hpp"

namespace py = pybind11;
using namespace tradeforge;

namespace {

EventType event_type_from_string(const std::string& name) {
    if (name == "ADD") return EventType::Add;
    if (name == "CANCEL") return EventType::Cancel;
    if (name == "MODIFY") return EventType::Modify;
    if (name == "REPLACE") return EventType::Replace;
    if (name == "TRADE") return EventType::Trade;
    if (name == "CLEAR") return EventType::Clear;
    if (name == "SNAPSHOT") return EventType::Snapshot;
    if (name == "HALT") return EventType::Halt;
    if (name == "RESUME") return EventType::Resume;
    throw IntegrityError("unknown event type " + name);
}

std::string event_type_to_string(EventType type) {
    switch (type) {
        case EventType::Add: return "ADD";
        case EventType::Cancel: return "CANCEL";
        case EventType::Modify: return "MODIFY";
        case EventType::Replace: return "REPLACE";
        case EventType::Trade: return "TRADE";
        case EventType::Clear: return "CLEAR";
        case EventType::Snapshot: return "SNAPSHOT";
        case EventType::Halt: return "HALT";
        case EventType::Resume: return "RESUME";
    }
    return "UNKNOWN";
}

TimeInForce tif_from_string(const std::string& name) {
    if (name == "DAY") return TimeInForce::Day;
    if (name == "GTC") return TimeInForce::Gtc;
    if (name == "IOC") return TimeInForce::Ioc;
    if (name == "FOK") return TimeInForce::Fok;
    throw IntegrityError("unknown time in force " + name);
}

// Convert a Python mapping into an Event. Explicit field by field: a
// `**kwargs`-style conversion would silently accept a renamed field.
Event event_from_mapping(const py::dict& payload) {
    Event event;
    event.sequence_id = payload.contains("sequence_id") ? payload["sequence_id"].cast<SequenceId>() : 0;
    event.exchange_timestamp_ns =
        payload.contains("exchange_timestamp_ns") ? payload["exchange_timestamp_ns"].cast<TimestampNs>() : 0;
    event.receive_timestamp_ns =
        payload.contains("receive_timestamp_ns") && !payload["receive_timestamp_ns"].is_none()
            ? payload["receive_timestamp_ns"].cast<TimestampNs>()
            : 0;
    event.symbol = payload.contains("symbol") ? payload["symbol"].cast<std::string>() : "";
    event.type = event_type_from_string(
        payload.contains("event_type") ? payload["event_type"].cast<std::string>() : "ADD");

    if (payload.contains("side") && !payload["side"].is_none()) {
        const std::string side = payload["side"].cast<std::string>();
        event.side = (side == "BUY") ? Side::Buy : Side::Sell;
        event.has_side = true;
    }
    if (payload.contains("price_ticks") && !payload["price_ticks"].is_none()) {
        event.price_ticks = payload["price_ticks"].cast<PriceTicks>();
    }
    if (payload.contains("quantity_base") && !payload["quantity_base"].is_none()) {
        event.quantity_base = payload["quantity_base"].cast<QuantityBase>();
    }
    if (payload.contains("order_id") && !payload["order_id"].is_none()) {
        event.order_id = payload["order_id"].cast<OrderId>();
        event.has_order_id = true;
    }
    if (payload.contains("flags") && !payload["flags"].is_none()) {
        event.flags = payload["flags"].cast<std::uint32_t>();
    }
    return event;
}

py::dict snapshot_to_mapping(const Snapshot& snapshot) {
    py::dict out;
    out["timestamp_ns"] = snapshot.timestamp_ns;
    out["sequence_id"] = snapshot.sequence_id;
    auto to_list = [](const std::vector<Level>& levels) {
        py::list items;
        for (const Level& level : levels) {
            py::dict entry;
            entry["price_ticks"] = level.price_ticks;
            entry["quantity_base"] = level.quantity_base;
            entry["order_count"] = level.order_count;
            items.append(entry);
        }
        return items;
    };
    out["bids"] = to_list(snapshot.bids);
    out["asks"] = to_list(snapshot.asks);
    return out;
}

py::dict stats_to_mapping(const BookStats& stats) {
    py::dict out;
    out["updates"] = stats.updates;
    out["trades"] = stats.trades;
    out["traded_volume_base"] = stats.traded_volume_base;
    out["skipped_unattributed_trades"] = stats.skipped_unattributed_trades;
    out["crossed_attempts"] = stats.crossed_attempts;
    out["locked_attempts"] = stats.locked_attempts;
    return out;
}

}  // namespace

PYBIND11_MODULE(tradeforge_core, m) {
    m.doc() = "TradeForge compiled core: L2 book reconstruction and matching. "
              "Prices are int64 ticks throughout; no floating-point price comparison "
              "exists in this module.";
    m.attr("__version__") = "0.1.0";
    m.attr("PRICE_TYPE") = "int64_ticks";
    m.attr("HAS_FLOAT_PRICE_COMPARISON") = false;

    py::register_exception<IntegrityError>(m, "IntegrityError");
    py::register_exception<CapabilityError>(m, "CapabilityError");

    py::class_<Level>(m, "Level")
        .def_readonly("price_ticks", &Level::price_ticks)
        .def_readonly("quantity_base", &Level::quantity_base)
        .def_readonly("order_count", &Level::order_count)
        .def("__repr__", [](const Level& level) {
            return "<Level " + std::to_string(level.price_ticks) + " x " +
                   std::to_string(level.quantity_base) + ">";
        });

    py::class_<MbpBook>(m, "MbpBook")
        .def(py::init<std::string, std::size_t, bool>(), py::arg("symbol"),
             py::arg("max_levels_per_side") = 500, py::arg("strict_crossed") = true)
        .def("apply", [](MbpBook& book, const py::dict& payload) {
            book.apply(event_from_mapping(payload));
        }, py::arg("event"))
        .def("snapshot", [](const MbpBook& book, std::size_t depth) {
            return snapshot_to_mapping(book.snapshot(depth));
        }, py::arg("depth") = 10)
        .def("level_size", [](const MbpBook& book, const std::string& side, PriceTicks price) {
            return book.level_size(side == "BUY" ? Side::Buy : Side::Sell, price);
        }, py::arg("side"), py::arg("price_ticks"))
        .def("clear", &MbpBook::clear)
        .def("stats", [](const MbpBook& book) { return stats_to_mapping(book.stats()); })
        .def_property_readonly("symbol", &MbpBook::symbol)
        .def_property_readonly("last_sequence_id", &MbpBook::last_sequence_id)
        .def_property_readonly("last_timestamp_ns", &MbpBook::last_timestamp_ns);

    py::class_<MatchFill>(m, "MatchFill")
        .def_readonly("price_ticks", &MatchFill::price_ticks)
        .def_readonly("quantity_base", &MatchFill::quantity_base);

    py::class_<MatchResult>(m, "MatchResult")
        .def_readonly("fills", &MatchResult::fills)
        .def_readonly("requested_base", &MatchResult::requested_base)
        .def_readonly("filled_base", &MatchResult::filled_base)
        .def_readonly("remaining_base", &MatchResult::remaining_base)
        .def_readonly("levels_crossed", &MatchResult::levels_crossed)
        .def_property_readonly("average_price_ticks", &MatchResult::average_price_ticks);

    m.def("match_order", [](const MbpBook& book, const std::string& side,
                            QuantityBase quantity_base, py::object limit_price,
                            const std::string& tif, std::size_t max_levels_crossed) {
        const Snapshot snapshot = book.snapshot(50);
        std::optional<PriceTicks> limit;
        if (!limit_price.is_none()) {
            limit = limit_price.cast<PriceTicks>();
        }
        return match_order(snapshot, side == "BUY" ? Side::Buy : Side::Sell, quantity_base,
                           limit, tif_from_string(tif), max_levels_crossed);
    }, py::arg("book"), py::arg("side"), py::arg("quantity_base"),
       py::arg("limit_price_ticks") = py::none(), py::arg("time_in_force") = "DAY",
       py::arg("max_levels_crossed") = 0);

    m.def("resting_price_ticks", [](const MbpBook& book, const std::string& side,
                                    PriceTicks offset) {
        const auto price = resting_price_ticks(
            book.snapshot(50), side == "BUY" ? Side::Buy : Side::Sell, offset);
        return price.has_value() ? py::cast(*price) : py::none();
    }, py::arg("book"), py::arg("side"), py::arg("offset_ticks") = 0);

    m.def("event_type_names", []() {
        return std::vector<std::string>{"ADD", "CANCEL", "MODIFY", "REPLACE", "TRADE",
                                        "CLEAR", "SNAPSHOT", "HALT", "RESUME"};
    });
    m.def("mutates_book", [](const std::string& name) {
        return mutates_book(event_type_from_string(name));
    });
    m.def("event_type_of", [](const std::string& name) {
        return event_type_to_string(event_type_from_string(name));
    });
}
