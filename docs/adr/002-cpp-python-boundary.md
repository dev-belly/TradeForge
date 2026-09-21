# ADR-002: C++ owns the event/book/matching core; Python owns research

* **Status:** Accepted
* **Date:** 2026-09-08

## Context

Two failure modes dominate open-source quant repos: (a) C++ exists only as a
README decoration, or (b) Python and C++ both implement the "real" logic and
drift apart.

## Decision

**C++20 owns** the hot, integer-tick, deterministic path: event decoding,
MBP order book, matching core, replay loop driver, queue primitives.

**Python owns** research, statistics, ML, experimentation, TCA, reporting, API,
CLI, dashboard, configuration.

**Binding** is pybind11 over a *narrow* façade: POD structs in/out
(`BookSnapshotView`, `MatchResult`, `SegmentResult`). The C++ object graph is not
exposed; no `py::class_` for internal node types.

**Single production core.** The Python book/matcher is a *reference oracle*:
used for differential tests and as an explicitly labelled fallback
(`engine_backend = "python-reference"`) when the extension is not compiled.
A differential test asserts identical snapshots on randomized event streams.

## Consequences

* One behavioural definition, two implementations, continuously cross-checked.
* Users without a compiler can still run `make demo` (labelled).
* Every new core behaviour must be added to both, which is a deliberate cost that
  keeps the two honest.
* pybind11 call overhead is measured (benchmark `python_cpp_boundary`) so we know
  the boundary is not the bottleneck.
