# ADR-001: Integer price ticks everywhere in the core

* **Status:** Accepted
* **Date:** 2026-09-08

## Context

Prices arrive as floats (LOBSTER: `13100.0` scaled by 10000; Binance: decimal
strings). Float equality and float accumulation are wrong tools for a matching
engine: `0.1 + 0.2 != 0.3`, and a price level key hashed on a float creates
duplicate levels for values that are economically identical.

## Decision

* All prices inside `domain`, `orderbook`, `matching`, `replay`, `queue` are
  `int64` **ticks**.
* `InstrumentSpec.tick_size` (a `Decimal`, not a float) converts to/from price.
* Conversion to float happens **only** at reporting / ML feature boundaries.
* Tick count is bounded by `price_ticks * tick_size` and validated against
  `InstrumentSpec.price_bounds`.

## Consequences

* Matching is exact and reproducible across platforms.
* Level keys are integers → dense arrays and hash maps are safe.
* Reporting code must remember to convert; `Money`/`Price` helpers exist so no
  one re-implements `ticks * 0.01`.
* Instruments with differing tick sizes must be configured, never assumed.
