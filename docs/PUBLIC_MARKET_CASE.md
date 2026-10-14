# Public depth capture: offline matching on real quotes

This case uses a saved 40-second DOGEUSDT public Binance depth episode, with
the REST snapshot and all WebSocket depth packets preserved in a small gzip
fixture. The decoder validates the snapshot bridge, update-ID continuity,
timestamps and exact price/quantity units. It applies an entire depth packet
before exposing a tradable state, so an intermediate partially updated book
cannot become a simulated fill.

```bash
python tools/replay_public_depth.py data/examples/public-depth-20261013.json.gz \
  --out artifacts/public-depth
```

The replay is offline and requires no API key. [Saved CSV results](public-depth/results.csv)
and [protocol / source hash](public-depth/results.json) contain all 18 cells:
immediate aggressive matching and ten equal-time slices, three hypothetical
order latencies (0, 1 and 10 ms), and three taker-fee scenarios (0, 5 and 10 bps).
Both schedules buy the same 1,000 DOGE over the declared 20-second window.
Immediate matching completes at the beginning of that window.

| At 0 ms hypothetical latency | 0 bps assumed fee | 5 bps assumed fee | 10 bps assumed fee |
| --- | --- | --- | --- |
| Immediate executed cost | 1.724 bps | 6.725 bps | 11.726 bps |
| Ten-slice executed cost | 1.914 bps | 6.915 bps | 11.916 bps |

Costs use the same decision-time midpoint as the reference and include only
executed quantity. The saved episode fills all quantities. In this episode,
changing the declared latency from 0 to 1 or 10 ms does not change execution
prices; that result is retained. Observed book age is recorded for every cell.

This is a real-data validation of the aggressive matching core, **not** a claim
of profitable trading or a calibrated venue execution simulator. The feed
aggregates changes at 100 ms and does not reveal intra-packet activity or order
identity. Exact passive queues, sub-millisecond market states, account-specific
fees, actual order latency and endogenous market impact are not measured. No
orders are routed, and hypothetical fills do not modify future market states.
Full OMS and passive-queue validation remain separate from this focused case.

To record another public episode, install `pip install -e '.[capture]'` and run:

```bash
python tools/capture_public_depth.py --symbol DOGEUSDT --seconds 40 \
  --out data/examples/another-episode.json.gz
```

The supported example uses integral base-asset units. Fractional-unit instruments
are rejected rather than rounded into incorrect quantities/notionals. Market
values are public observations from the documented venue endpoints; this case
does not assign a synthetic-data license to third-party market observations.
