# Examples

Runnable scripts, each demonstrating one part of the platform. They use the
bundled synthetic generator, so none of them needs a network connection or an
API key.

| Script | Demonstrates |
|---|---|
| `01_reconstruct_the_book.py` | the event loop, book snapshots, immutable value objects |
| `02_run_one_execution.py` | a full TCA report: provenance, benchmarks, attribution, markouts |
| `03_queue_sensitivity.py` | the L2 queue assumption and the spread it produces |
| `04_paired_comparison.py` | why a point estimate is not a result |
| `05_custom_policy.py` | implementing and running a policy against the simulator |

Run one from the repository root:

```bash
python examples/02_run_one_execution.py
```

If the package is not installed, prefix with `PYTHONPATH=src`:

```bash
PYTHONPATH=src python examples/02_run_one_execution.py
```

Every script prints its provenance and, where a number could be misread, the
reason it should not be.
