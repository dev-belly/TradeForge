// C++ unit tests for the book and the matching engine.
//
// These are deliberately independent of the Python suite: the point of having
// two implementations is that they can be tested separately. The differential
// test (Python) is what asserts they agree.

#include <gtest/gtest.h>

#include <string>

#include "tradeforge/book.hpp"
#include "tradeforge/matching.hpp"
#include "tradeforge/types.hpp"

namespace tradeforge {
namespace {

Event add(Side side, PriceTicks price, QuantityBase quantity, SequenceId seq = 1) {
    Event event;
    event.sequence_id = seq;
    event.exchange_timestamp_ns = seq;
    event.type = EventType::Add;
    event.side = side;
    event.has_side = true;
    event.price_ticks = price;
    event.quantity_base = quantity;
    event.symbol = "TEST";
    return event;
}

Event cancel(Side side, PriceTicks price, QuantityBase quantity, SequenceId seq = 1) {
    Event event = add(side, price, quantity, seq);
    event.type = EventType::Cancel;
    return event;
}

Event trade(Side aggressor, PriceTicks price, QuantityBase quantity, SequenceId seq = 1) {
    Event event = add(opposite(aggressor), price, quantity, seq);
    event.type = EventType::Trade;
    event.side = aggressor;
    event.has_side = true;
    event.flags = aggressor == Side::Buy ? kFlagAggressorBuy : kFlagAggressorSell;
    return event;
}

MbpBook seeded() {
    MbpBook book("TEST");
    book.apply(add(Side::Buy, 9999, 500, 1));
    book.apply(add(Side::Buy, 9998, 700, 2));
    book.apply(add(Side::Sell, 10001, 400, 3));
    book.apply(add(Side::Sell, 10002, 900, 4));
    return book;
}

TEST(MbpBookTest, AddAndSnapshot) {
    MbpBook book = seeded();
    const Snapshot snapshot = book.snapshot(10);
    ASSERT_EQ(snapshot.bids.size(), 2U);
    ASSERT_EQ(snapshot.asks.size(), 2U);
    EXPECT_EQ(snapshot.best_bid().value(), 9999);
    EXPECT_EQ(snapshot.best_ask().value(), 10001);
    EXPECT_EQ(snapshot.bids.front().quantity_base, 500);
}

TEST(MbpBookTest, LevelsAreBestFirstOnBothSides) {
    MbpBook book = seeded();
    const Snapshot snapshot = book.snapshot(10);
    EXPECT_GT(snapshot.bids[0].price_ticks, snapshot.bids[1].price_ticks);
    EXPECT_LT(snapshot.asks[0].price_ticks, snapshot.asks[1].price_ticks);
}

TEST(MbpBookTest, AddsAccumulateAtTheSamePrice) {
    MbpBook book = seeded();
    book.apply(add(Side::Buy, 9999, 250, 5));
    EXPECT_EQ(book.level_size(Side::Buy, 9999), 750);
}

TEST(MbpBookTest, CancelRemovesEmptyLevels) {
    MbpBook book = seeded();
    book.apply(cancel(Side::Buy, 9998, 700, 5));
    EXPECT_EQ(book.level_size(Side::Buy, 9998), 0);
    EXPECT_EQ(book.snapshot(10).bids.size(), 1U);
}

TEST(MbpBookTest, CancelAtEmptyPriceThrows) {
    MbpBook book = seeded();
    EXPECT_THROW(book.apply(cancel(Side::Buy, 9000, 10, 5)), IntegrityError);
}

TEST(MbpBookTest, CancelBeyondDepthThrows) {
    MbpBook book = seeded();
    EXPECT_THROW(book.apply(cancel(Side::Buy, 9998, 701, 5)), IntegrityError);
}

TEST(MbpBookTest, TradeConsumesTheRestingSide) {
    MbpBook book = seeded();
    book.apply(trade(Side::Sell, 9999, 200, 5));
    EXPECT_EQ(book.level_size(Side::Buy, 9999), 300);
    EXPECT_EQ(book.stats().trades, 1U);
    EXPECT_EQ(book.stats().traded_volume_base, 200);
}

TEST(MbpBookTest, TradeExceedingDepthThrows) {
    MbpBook book = seeded();
    EXPECT_THROW(book.apply(trade(Side::Sell, 9999, 501, 5)), IntegrityError);
}

TEST(MbpBookTest, UnattributedTradeIsSkippedNotGuessed) {
    MbpBook book("TEST");
    book.apply(add(Side::Buy, 9999, 500, 1));
    book.apply(add(Side::Sell, 10001, 400, 2));

    Event event;
    event.sequence_id = 3;
    event.exchange_timestamp_ns = 3;
    event.type = EventType::Trade;
    event.price_ticks = 9999;
    event.quantity_base = 100;
    event.has_side = false;
    event.symbol = "TEST";
    book.apply(event);

    // Depth is untouched and the skip is counted.
    EXPECT_EQ(book.level_size(Side::Buy, 9999), 500);
    EXPECT_EQ(book.stats().skipped_unattributed_trades, 1U);
}

TEST(MbpBookTest, ModifyIsRejectedWithoutOrderIdentity) {
    MbpBook book = seeded();
    Event event = add(Side::Buy, 9999, 100, 5);
    event.type = EventType::Modify;
    EXPECT_THROW(book.apply(event), IntegrityError);
}

TEST(MbpBookTest, SymbolMismatchThrows) {
    MbpBook book = seeded();
    Event event = add(Side::Buy, 9999, 100, 5);
    event.symbol = "OTHER";
    EXPECT_THROW(book.apply(event), IntegrityError);
}

TEST(MbpBookTest, CrossedBookIsRejectedInStrictMode) {
    MbpBook book("TEST");
    book.apply(add(Side::Sell, 10001, 100, 1));
    // A bid above the best ask would cross the market.
    EXPECT_THROW(book.apply(add(Side::Buy, 10002, 100, 2)), IntegrityError);
    EXPECT_EQ(book.stats().crossed_attempts, 1U);
}

TEST(MbpBookTest, LockedMarketIsToleratedAndCounted) {
    MbpBook book("TEST");
    book.apply(add(Side::Buy, 10000, 100, 1));
    book.apply(add(Side::Sell, 10000, 100, 2));
    EXPECT_EQ(book.snapshot(10).best_bid().value(), 10000);
    EXPECT_EQ(book.snapshot(10).best_ask().value(), 10000);
    EXPECT_EQ(book.stats().locked_attempts, 1U);
}

TEST(MbpBookTest, ClearEmptiesBothSides) {
    MbpBook book = seeded();
    Event event;
    event.sequence_id = 9;
    event.exchange_timestamp_ns = 9;
    event.type = EventType::Clear;
    event.symbol = "TEST";
    book.apply(event);
    EXPECT_FALSE(book.snapshot(10).best_bid().has_value());
    EXPECT_FALSE(book.snapshot(10).best_ask().has_value());
}

TEST(MbpBookTest, HaltDoesNotMutateTheBook) {
    MbpBook book = seeded();
    Event event;
    event.sequence_id = 9;
    event.exchange_timestamp_ns = 9;
    event.type = EventType::Halt;
    event.symbol = "TEST";
    book.apply(event);
    EXPECT_EQ(book.level_size(Side::Buy, 9999), 500);
}

TEST(MbpBookTest, SnapshotDepthIsRespected) {
    MbpBook book = seeded();
    EXPECT_EQ(book.snapshot(1).bids.size(), 1U);
    EXPECT_EQ(book.snapshot(1).asks.size(), 1U);
}

TEST(MbpBookTest, SequenceAndTimestampAreTracked) {
    MbpBook book = seeded();
    EXPECT_EQ(book.last_sequence_id(), 4);
    EXPECT_EQ(book.last_timestamp_ns(), 4);
}

// --------------------------------------------------------------- matching

TEST(MatchingTest, MarketBuyTakesTheBestAskFirst) {
    MbpBook book = seeded();
    const auto result = match_order(book.snapshot(10), Side::Buy, 250, std::nullopt);
    EXPECT_EQ(result.filled_base, 250);
    EXPECT_EQ(result.levels_crossed, 1U);
    EXPECT_DOUBLE_EQ(result.average_price_ticks().value(), 10001.0);
}

TEST(MatchingTest, WalkingLevelsPricesEachOne) {
    MbpBook book = seeded();
    const auto result = match_order(book.snapshot(10), Side::Buy, 600, std::nullopt);
    EXPECT_EQ(result.filled_base, 600);
    EXPECT_EQ(result.levels_crossed, 2U);
    EXPECT_EQ(result.fills.back().price_ticks, 10002);
}

TEST(MatchingTest, LimitPriceStopsTheSweep) {
    MbpBook book = seeded();
    const auto result = match_order(book.snapshot(10), Side::Buy, 600, PriceTicks{10001});
    EXPECT_EQ(result.filled_base, 400);
    EXPECT_EQ(result.remaining_base, 200);
}

TEST(MatchingTest, FokIsAllOrNothing) {
    MbpBook book = seeded();
    const auto result =
        match_order(book.snapshot(10), Side::Buy, 600, PriceTicks{10001}, TimeInForce::Fok);
    EXPECT_EQ(result.filled_base, 0);
    EXPECT_TRUE(result.is_empty());
}

TEST(MatchingTest, IocKeepsTheCrossedPartAndReportsNoRemainder) {
    MbpBook book = seeded();
    const auto result =
        match_order(book.snapshot(10), Side::Buy, 600, PriceTicks{10001}, TimeInForce::Ioc);
    EXPECT_EQ(result.filled_base, 400);
    EXPECT_EQ(result.remaining_base, 0);
}

TEST(MatchingTest, MaxLevelsCrossedCapsTheSweep) {
    MbpBook book = seeded();
    const auto result = match_order(book.snapshot(10), Side::Buy, 2000, std::nullopt,
                                    TimeInForce::Day, 1);
    EXPECT_EQ(result.levels_crossed, 1U);
    EXPECT_EQ(result.filled_base, 400);
}

TEST(MatchingTest, EmptyBookFillsNothing) {
    MbpBook book("TEST");
    const auto result = match_order(book.snapshot(10), Side::Buy, 100, std::nullopt);
    EXPECT_EQ(result.filled_base, 0);
    EXPECT_EQ(result.remaining_base, 100);
    EXPECT_FALSE(result.average_price_ticks().has_value());
}

TEST(MatchingTest, NonPositiveQuantityThrows) {
    MbpBook book = seeded();
    EXPECT_THROW(match_order(book.snapshot(10), Side::Buy, 0, std::nullopt), IntegrityError);
}

TEST(MatchingTest, SellWalksTheBidSide) {
    MbpBook book = seeded();
    const auto result = match_order(book.snapshot(10), Side::Sell, 600, std::nullopt);
    EXPECT_EQ(result.filled_base, 600);
    EXPECT_EQ(result.fills.back().price_ticks, 9998);
    EXPECT_LT(result.average_price_ticks().value(), 9999.0);
}

TEST(MatchingTest, MarketOrderNeverFillsAtTheMid) {
    MbpBook book = seeded();
    const auto result = match_order(book.snapshot(10), Side::Buy, 100, std::nullopt);
    EXPECT_GT(result.average_price_ticks().value(), 10000.0);
}

TEST(MatchingTest, RestingPriceForABuyIsTheBidNotTheAsk) {
    MbpBook book = seeded();
    const auto price = resting_price_ticks(book.snapshot(10), Side::Buy, 0);
    ASSERT_TRUE(price.has_value());
    EXPECT_EQ(*price, 9999);
}

TEST(MatchingTest, RestingPriceForASellIsTheAsk) {
    MbpBook book = seeded();
    const auto price = resting_price_ticks(book.snapshot(10), Side::Sell, 0);
    ASSERT_TRUE(price.has_value());
    EXPECT_EQ(*price, 10001);
}

TEST(MatchingTest, RestingPriceOnAnEmptySideIsAbsent) {
    MbpBook book("TEST");
    EXPECT_FALSE(resting_price_ticks(book.snapshot(10), Side::Buy, 0).has_value());
}

TEST(MatchingTest, MarketabilityUsesTheCorrectTouch) {
    MbpBook book = seeded();
    const Snapshot snapshot = book.snapshot(10);
    EXPECT_TRUE(is_marketable(snapshot, Side::Buy, PriceTicks{10001}));
    EXPECT_TRUE(is_marketable(snapshot, Side::Buy, PriceTicks{10050}));
    EXPECT_FALSE(is_marketable(snapshot, Side::Buy, PriceTicks{10000}));
    EXPECT_TRUE(is_marketable(snapshot, Side::Sell, PriceTicks{9999}));
    EXPECT_FALSE(is_marketable(snapshot, Side::Sell, PriceTicks{10000}));
}

TEST(MatchingTest, SimulatedFillsDoNotMutateTheBook) {
    // Documented replay-approximation behaviour: our orders were not in the
    // historical stream, so consuming depth here would double-count.
    MbpBook book = seeded();
    const auto before = book.level_size(Side::Sell, 10001);
    match_order(book.snapshot(10), Side::Buy, 400, std::nullopt);
    EXPECT_EQ(book.level_size(Side::Sell, 10001), before);
}

TEST(TypesTest, SideSignsAndOpposites) {
    EXPECT_EQ(sign(Side::Buy), 1);
    EXPECT_EQ(sign(Side::Sell), -1);
    EXPECT_EQ(opposite(Side::Buy), Side::Sell);
    EXPECT_EQ(opposite(Side::Sell), Side::Buy);
}

TEST(TypesTest, MutatingEventTypes) {
    EXPECT_TRUE(mutates_book(EventType::Add));
    EXPECT_TRUE(mutates_book(EventType::Trade));
    EXPECT_TRUE(mutates_book(EventType::Clear));
    EXPECT_FALSE(mutates_book(EventType::Halt));
    EXPECT_FALSE(mutates_book(EventType::Resume));
}

TEST(EventTest, OrderingKeyIsTimestampThenSequence) {
    Event first;
    first.exchange_timestamp_ns = 100;
    first.sequence_id = 5;
    Event second;
    second.exchange_timestamp_ns = 100;
    second.sequence_id = 6;
    Event third;
    third.exchange_timestamp_ns = 101;
    third.sequence_id = 1;

    EXPECT_TRUE(first.precedes(second));
    EXPECT_TRUE(second.precedes(third));
    EXPECT_FALSE(third.precedes(second));
}

}  // namespace
}  // namespace tradeforge
