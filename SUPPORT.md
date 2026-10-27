# Support

Market microstructure and execution research with a C++20 order book, Python research tools, and transaction-cost analysis.

## Start here

Read the [README](README.md) for setup, examples and method boundaries. The tested maintenance commands are defined in the [CI workflow](.github/workflows/ci.yml). Environment: Python 3.11+; C++20 tooling for the optional compiled core.

```bash
python -m venv .venv
# Activate .venv using your shell, then:
python -m pip install -e ".[dev,full]"
```

State whether the Python reference backend or compiled C++ backend is in use. For C++ changes, also follow the CMake, differential, and sanitizer checks in the CI workflow. Keep exchange credentials, private order flow, account IDs, positions and licensed feeds out of public reports.

## Ask a question or report a problem

Use [GitHub Issues](https://github.com/dev-belly/TradeForge/issues) for usage questions, reproducible bugs and scoped feature proposals. English and Chinese reports are welcome. Check existing issues first and use the bug or feature form when it fits. Support is best effort; no response time or delivery date is promised.

Include the backend, seed, event order, instrument settings, fee/latency assumptions, and data capability flags. Prefer the saved public-depth fixture or a minimal synthetic sequence. Separate observed market events from modeled fills, fees and queue assumptions.

Include the commit SHA, operating system, dependency versions, exact command, expected behavior and a minimal reproduction. Share only redacted logs and data you are allowed to publish. Remove credentials, personal information and confidential or licensed records. For a suspected security vulnerability, follow [SECURITY.md](SECURITY.md) before posting details.

## Verify locally

Run relevant checks before reporting a regression or opening a pull request:

```bash
python -m ruff check src tests examples benchmarks tools
python -m ruff format --check src tests examples benchmarks tools
python -m mypy src/tradeforge
python -m pytest tests -q
```

These are verification commands, not a claim that a new environment has already passed them. For changes that affect results, retain input provenance, configuration and seeds alongside the output.
