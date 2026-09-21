# Data

This directory is where normalized event streams are written when a run persists
its input (`configs/market_data.yaml` → `storage.normalized_dir`).

Nothing here is committed. The bundled generator produces its stream in memory,
so a fresh checkout has no data and does not need any:

```bash
make demo        # deterministic synthetic session, generated on the fly
make run-all     # writes Parquet artefacts to artifacts/runs/
```

To use a real source, add an adapter under `src/tradeforge/data/adapters/` and
declare its capability tier honestly. See `docs/guides/data.md`.

**No market data is redistributed with this repository.** The only bundled source
is a deterministic synthetic generator, and every artefact it produces is
labelled `SYNTHETIC - deterministic generator, NOT real market data`.
