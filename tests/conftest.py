"""Shared fixtures.

Fixtures are built from the real `configs/` directory rather than from literals,
so a change to a config that breaks the system shows up in the test suite instead
of only in production. Nothing here mocks the domain: the point of these tests is
to exercise the real objects.
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

CONFIGS_DIR = ROOT / "configs"
SQL_DIR = ROOT / "sql"


@pytest.fixture(scope="session")
def project_root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def configs_dir() -> Path:
    return CONFIGS_DIR


@pytest.fixture(scope="session")
def configs() -> dict:
    from tradeforge.infrastructure.config import load_configs, validate_required

    loaded = load_configs(CONFIGS_DIR)
    validate_required(loaded)
    return loaded


@pytest.fixture
def instrument():
    from tradeforge.domain.instrument import InstrumentSpec

    return InstrumentSpec(
        symbol="TEST",
        tick_size=Decimal("0.01"),
        lot_size=10,
        currency="USD",
        price_band_lower_ticks=5_000,
        price_band_upper_ticks=20_000,
    )


@pytest.fixture
def book_settings():
    from tradeforge.orderbook import BookSettings

    return BookSettings.from_dict({})


@pytest.fixture
def mbp_book(book_settings):
    from tradeforge.domain.enums import DataType
    from tradeforge.orderbook import create_book

    return create_book(symbol="TEST", settings=book_settings, data_type=DataType.L2_MBP)


@pytest.fixture
def mbo_settings(book_settings):
    """MBP settings with the book mode switched to MBO (settings are frozen)."""
    from dataclasses import replace

    from tradeforge.domain.enums import BookMode

    return replace(book_settings, mode=BookMode.MBO)


@pytest.fixture
def mbo_book(mbo_settings):
    from tradeforge.domain.enums import DataType
    from tradeforge.orderbook import create_book

    return create_book(symbol="TEST", settings=mbo_settings, data_type=DataType.L3_MBO)


@pytest.fixture
def seeded_book(mbp_book):
    """A two-sided book: bid 9999 x 500, ask 10001 x 400, plus deeper levels."""
    from tradeforge.domain.enums import EventType, Side
    from tradeforge.domain.events import MarketEvent

    levels = [
        (Side.BUY, 9999, 500),
        (Side.BUY, 9998, 700),
        (Side.BUY, 9997, 300),
        (Side.SELL, 10001, 400),
        (Side.SELL, 10002, 900),
        (Side.SELL, 10003, 250),
    ]
    for index, (side, price, quantity) in enumerate(levels):
        mbp_book.apply(
            MarketEvent(
                sequence_id=index,
                exchange_timestamp_ns=1_000 + index,
                symbol="TEST",
                event_type=EventType.ADD,
                side=side,
                price_ticks=price,
                quantity_base=quantity,
                source="fixture",
            )
        )
    return mbp_book


@pytest.fixture
def parent_order():
    from tradeforge.domain.enums import Side
    from tradeforge.domain.orders import ParentOrder

    return ParentOrder(
        parent_order_id="test-parent",
        symbol="TEST",
        side=Side.BUY,
        quantity_base=10_000,
        start_ns=1_000_000_000,
        end_ns=1_800_000_000_000,
        participation_limit=0.10,
    )


@pytest.fixture
def synthetic_events():
    from tradeforge.data.registry import create_adapter

    def _make(n_events: int = 4_000, seed: int = 7):
        adapter = create_adapter(
            "synthetic",
            {"seed": seed, "n_events": n_events, "start_time_ns": 34_200_000_000_000},
        )
        return adapter

    return _make
