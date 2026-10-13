"""Record a short public Binance depth episode; never sends orders or uses keys.

Install the optional recorder with `pip install -e '.[capture]'`. Raw packets
are retained so sequence continuity and unit conversion can be checked offline.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import time
import urllib.request
from pathlib import Path
from typing import Any


def get_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "TradeForge-public-research/0.1"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read())


async def capture(symbol: str, seconds: float, destination: Path) -> None:
    import websockets

    if seconds < 10 or seconds > 300:
        raise ValueError("choose a capture duration between 10 and 300 seconds")
    symbol = symbol.upper()
    if not symbol.isascii() or not symbol.isalnum():
        raise ValueError("symbol must contain ASCII letters/numbers only")
    base = "https://data-api.binance.vision/api/v3/"
    info = await asyncio.to_thread(get_json, f"{base}exchangeInfo?symbol={symbol}")
    specification = info["symbols"][0]
    filters = {item["filterType"]: item for item in specification["filters"]}
    url = f"wss://data-stream.binance.vision:443/ws/{symbol.lower()}@depth@100ms"
    packets = []
    async with websockets.connect(url, open_timeout=15, max_queue=4096) as socket:
        packets.append(json.loads(await asyncio.wait_for(socket.recv(), timeout=10)))
        snapshot = await asyncio.to_thread(get_json, f"{base}depth?symbol={symbol}&limit=100")
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            remaining = until - time.monotonic()
            try:
                packets.append(json.loads(await asyncio.wait_for(socket.recv(), timeout=remaining)))
            except TimeoutError:
                break
    payload = {
        "schema_version": 1,
        "data_kind": "public_market_capture",
        "symbol": symbol,
        "quote_currency": specification["quoteAsset"],
        "tick_size": filters["PRICE_FILTER"]["tickSize"],
        "quantity_step": filters["LOT_SIZE"]["stepSize"],
        "source": {
            "snapshot_url": f"{base}depth?symbol={symbol}&limit=100",
            "stream_url": url,
            "docs": "https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md",
        },
        "snapshot": snapshot,
        "depth_events": packets,
        "scope": "Public aggregated depth; no order identity, executions or measured order latency",
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n").encode()
    destination.write_bytes(gzip.compress(raw, mtime=0))
    print(
        json.dumps(
            {
                "path": str(destination),
                "depth_packets": len(packets),
                "compressed_bytes": destination.stat().st_size,
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="DOGEUSDT")
    parser.add_argument("--seconds", type=float, default=40)
    parser.add_argument("--out", type=Path, default=Path("data/examples/public-depth.json.gz"))
    args = parser.parse_args()
    asyncio.run(capture(args.symbol, args.seconds, args.out))


if __name__ == "__main__":
    main()
