"""Streamlit dashboard.

The dashboard reads artefacts; it does not produce results. If there is no data
it says so, loudly, and offers the command that would create some. The failure
mode this avoids is a dashboard that renders empty charts, which a reader
interprets as "the strategies all performed the same" rather than "nothing ran".

Every figure carries its provenance caption. A chart of cost by policy without
"SYNTHETIC data, approximate queue, scenario latency" is a chart that will be
screenshotted and quoted out of context.

Each tab is a distinct question, so each is a named function rather than a block
inside one long `render`.

Start it with `make dashboard`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

# `streamlit run <file>` executes this file as a *script*, not as a module in the
# `tradeforge.interfaces` package, so `__package__` is empty and every relative
# import fails with "attempted relative import with no known parent package".
# The dashboard then renders that ImportError into the page while the HTTP status
# stays 200 - which is why a `curl` check would never have caught it.
#
# Bootstrapping `src/` onto the path and importing absolutely makes the file work
# both as a script and as a module.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from tradeforge.storage import DuckDbStore, StoreStatus  # noqa: E402

ARTIFACT_ROOT = Path("artifacts/runs")
SQL_DIR = Path("sql")
REPORT_DIR = Path("artifacts/reports")
RESEARCH_DIR = Path("artifacts/research")

PROVENANCE_CAPTION = (
    "SYNTHETIC data (deterministic generator, not a real venue) - "
    "replay approximation: our orders did not alter the event stream"
)

TABS = (
    "Cost by algorithm",
    "Benchmark disagreement",
    "Attribution",
    "Markouts",
    "Queue sensitivity",
    "Experiment artefacts",
)


def _store() -> DuckDbStore:
    return DuckDbStore(ARTIFACT_ROOT, sql_dir=SQL_DIR)


def _empty_state(message: str, command: str) -> None:
    """An empty store is stated, never rendered as an empty chart."""
    st.warning(message)
    st.code(command, language="bash")
    st.stop()


def render() -> None:
    st.set_page_config(page_title="TradeForge", layout="wide")
    st.title("TradeForge")
    st.caption(
        "Market microstructure, limit order book and execution research. "
        "Read-only view over generated artefacts."
    )

    store = _store()
    status = store.status()
    _render_sidebar(status)

    if not status.present_tables:
        _empty_state(
            "No Parquet artefacts found, so there is nothing to display. "
            "An empty dashboard is not evidence that the strategies performed "
            "identically - it means no run has been recorded yet.",
            "make run-all   # or: tradeforge research run",
        )

    tabs = st.tabs(list(TABS))
    with tabs[0]:
        _tab_cost(store)
    with tabs[1]:
        _tab_benchmarks(store)
    with tabs[2]:
        _tab_attribution(store)
    with tabs[3]:
        _tab_markouts(store)
    with tabs[4]:
        _tab_queue(store)
    with tabs[5]:
        _tab_artefacts()


def _render_sidebar(status: StoreStatus) -> None:
    with st.sidebar:
        st.header("Store")
        st.write(f"Root: `{status.root}`")
        if status.present_tables:
            st.success(f"{len(status.present_tables)} table(s) with data")
        else:
            st.error("no tables with data")
        if status.missing_tables:
            st.caption("absent: " + ", ".join(status.missing_tables))
        st.divider()
        st.caption(
            "Every figure below is produced from SYNTHETIC data. Queue position is "
            "an estimate from aggregated levels; latency is a scenario, not a "
            "measurement."
        )


# ---------------------------------------------------------------------- tabs


def _tab_cost(store: DuckDbStore) -> None:
    st.subheader("Cost and completion by algorithm")
    st.dataframe(store.run_named("01_execution_summary"), width="stretch", hide_index=True)
    st.caption(PROVENANCE_CAPTION)


def _tab_benchmarks(store: DuckDbStore) -> None:
    st.subheader("How much does the benchmark choice change the story?")
    st.markdown(
        "A strategy that looks strong against the **arrival price** can look "
        "weak against the **interval VWAP**, because the arrival benchmark "
        "credits the strategy with market drift that happened while the order "
        "was working. `arrival_minus_vwap_bps` is the size of that illusion."
    )
    frame = store.run_named("02_benchmark_disagreement")
    st.dataframe(frame, width="stretch", hide_index=True)
    if not frame.empty and "arrival_minus_vwap_bps" in frame.columns:
        st.bar_chart(frame.set_index("policy")["arrival_minus_vwap_bps"], height=320)
    st.caption(PROVENANCE_CAPTION)


def _tab_attribution(store: DuckDbStore) -> None:
    st.subheader("Where the basis points went")
    st.markdown(
        "The decomposition is additive by construction. The **residual** absorbs "
        "denominator approximation plus any genuine impact, which this platform "
        "does **not** claim to identify from a replay in which our orders never "
        "altered the tape."
    )
    frame = store.run_named("03_cost_attribution")
    st.dataframe(frame, width="stretch", hide_index=True)
    if not frame.empty and "residual_share_of_is" in frame.columns:
        high = frame[frame["residual_share_of_is"].abs() > 0.5]
        if not high.empty:
            st.warning(
                "For these policies the residual explains over half the cost. "
                "The decomposition is not describing those executions well, and "
                "the component columns should not be quoted."
            )
    st.caption(PROVENANCE_CAPTION)


def _tab_markouts(store: DuckDbStore) -> None:
    st.subheader("Were the fills adversely selected?")
    st.markdown(
        "Positive is adverse. A short-horizon negative markout turning positive "
        "at longer horizons is the signature of adverse selection on passive "
        "fills - invisible in an average fill price."
    )
    st.dataframe(
        store.run_named("04_markout_adverse_selection"),
        width="stretch",
        hide_index=True,
    )
    st.caption(PROVENANCE_CAPTION)


def _tab_queue(store: DuckDbStore) -> None:
    st.subheader("How much does the L2 queue assumption move the answer?")
    st.markdown(
        "With aggregated level data the queue position is unobservable. The "
        "honest treatment is to vary the assumption and report the spread: "
        "`policy_spread_bps` is the error bar on any passive-execution claim made "
        "from L2 data. Read it next to the fill ratio - when fills are "
        "near-certain, the assumption has little left to decide."
    )
    st.dataframe(
        store.run_named("05_queue_policy_sensitivity"),
        width="stretch",
        hide_index=True,
    )
    st.caption(PROVENANCE_CAPTION)


def _tab_artefacts() -> None:
    st.subheader("Experiment artefacts")
    summaries = sorted(RESEARCH_DIR.glob("*.summary.json")) if RESEARCH_DIR.is_dir() else []
    if not summaries:
        st.info(
            f"No experiment summaries under `{RESEARCH_DIR}`. Run `make research` to generate them."
        )
    for path in summaries:
        _render_summary(path)

    reports = sorted(REPORT_DIR.glob("*.html")) if REPORT_DIR.is_dir() else []
    if reports:
        st.divider()
        st.subheader("Generated HTML reports")
        for report in reports:
            st.write(f"- `{report.name}`")


def _render_summary(path: Path) -> None:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    seeds = payload.get("seeds", [])
    with st.expander(f"{payload.get('name', path.stem)} ({len(seeds)} seeds)"):
        environment = payload.get("environment", {})
        st.caption(
            f"commit {environment.get('git_commit', 'unknown')} - "
            f"dirty: {environment.get('git_dirty')} - "
            f"config {payload.get('config_fingerprint')}"
        )
        cells = payload.get("summary", {}).get("cells", [])
        if cells:
            st.dataframe(pd.DataFrame(cells), width="stretch", hide_index=True)
        for caveat in payload.get("caveats", []):
            st.caption(f"- {caveat}")


def main() -> None:  # pragma: no cover - manual entry point
    render()


if __name__ == "__main__":  # pragma: no cover
    main()
