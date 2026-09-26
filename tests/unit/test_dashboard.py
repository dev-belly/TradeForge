"""The Streamlit dashboard.

This suite exists because `make dashboard` was **completely broken** and nothing
noticed. `streamlit run <file>` executes the file as a script rather than as a
module in the package, so every relative import failed with "attempted relative
import with no known parent package". Streamlit renders that ImportError into the
page while the HTTP status stays 200, so a `curl` check reports success.

`streamlit.testing.v1.AppTest` runs the script the same way the server does, so
it reproduces the failure without a browser or a running server.

**The dashboard has two states and both are tested.** With no artefacts under
`artifacts/runs/` it warns and stops; with artefacts it renders six tabs. An
earlier version of this file asserted the six-tab branch unconditionally. It
passed locally - where `artifacts/runs/` happened to be populated - and failed on
CI, where a fresh checkout has no artefacts. A test whose result depends on
ambient state reports the machine, not the code.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

streamlit = pytest.importorskip("streamlit", reason="the dashboard needs streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "src/tradeforge/interfaces/dashboard.py"

EXPECTED_TABS = (
    "Cost by algorithm",
    "Benchmark disagreement",
    "Attribution",
    "Markouts",
    "Queue sensitivity",
    "Experiment artefacts",
)


@pytest.fixture(scope="module")
def rendered() -> AppTest:
    """The dashboard, run the way the server runs it.

    Module-scoped because a cold render imports pandas and streamlit; doing it
    once keeps the suite quick.
    """
    app = AppTest.from_file(str(DASHBOARD), default_timeout=120)
    app.run()
    return app


def store_has_data() -> bool:
    """Whether `artifacts/runs/` holds anything, which selects the render branch."""
    runs = ROOT / "artifacts/runs"
    if not runs.is_dir():
        return False
    return any(path.is_dir() and any(path.iterdir()) for path in runs.iterdir())


class TestItRuns:
    def test_renders_without_raising(self, rendered):
        """The load-bearing assertion, valid in both states.

        Before the fix this reported an ImportError from the first relative
        import, which is what a user saw when they ran `make dashboard`.
        """
        assert not rendered.exception, "the dashboard raised while rendering:\n" + "\n".join(
            str(e.value) for e in rendered.exception
        )

    def test_the_page_names_the_engine_backend(self, rendered):
        """Which engine produced a number is part of the number.

        Checked against the rendered captions rather than the source text: the
        source could mention the engine in a comment and satisfy a string match
        while the page shows nothing.
        """
        captions = " ".join(c.value for c in rendered.caption)
        assert "Engine:" in captions, (
            "the dashboard must say which engine produced the numbers it shows; "
            f"captions were: {captions[:300]}"
        )
        assert "python-reference" in captions or "cpp" in captions

    def test_runs_as_a_script_not_a_module(self):
        """`AppTest` and `streamlit run` both execute the file directly.

        If this ever stops being true the suite would be testing a code path no
        user takes, so the relative-import bug could return unnoticed.
        """
        source = DASHBOARD.read_text(encoding="utf-8")
        assert "from tradeforge." in source, (
            "the dashboard uses absolute imports so it works as a script; a "
            "relative import would fail under `streamlit run`"
        )
        relative = re.findall(r"^from \.\.", source, flags=re.MULTILINE)
        assert not relative, f"relative imports cannot work as a script: {relative}"


class TestDeclaredTabs:
    """Source-level, so these hold regardless of what is on disk."""

    def test_declares_the_expected_tabs(self):
        from tradeforge.interfaces.dashboard import TABS

        assert tuple(TABS) == EXPECTED_TABS

    def test_tab_labels_are_distinct(self):
        """A duplicate label makes the screenshot tool's `click_tab` select the
        first match, silently capturing the same tab twice."""
        from tradeforge.interfaces.dashboard import TABS

        assert len(set(TABS)) == len(TABS)

    def test_every_tab_carries_the_provenance_caption(self):
        """Every figure on the page is from the generator, and the page says so.

        A screenshot of a cost table without this caption is a screenshot that
        gets quoted as if it came from a real venue.
        """
        source = DASHBOARD.read_text(encoding="utf-8")
        assert "SYNTHETIC" in source
        assert source.count("PROVENANCE_CAPTION") >= 5, (
            "every data tab should carry the provenance caption"
        )


class TestEmptyStore:
    """The branch CI exercises, and the one a new user hits first."""

    def test_states_the_command_that_populates_the_store(self, rendered):
        if store_has_data():
            pytest.skip("artefacts/runs is populated, so the render branch is active")
        assert rendered.warning, (
            "with no artefacts the dashboard must say so rather than drawing "
            "empty axes, which read as 'the strategies performed identically'"
        )
        message = " ".join(w.value for w in rendered.warning)
        assert "not evidence" in message, (
            "the empty state must say what an empty dashboard does NOT mean"
        )
        # The command lives in its own code block, not in the warning text.
        commands = " ".join(block.value for block in rendered.code)
        assert "run-all" in commands, (
            f"the empty state must name the command that fixes it; code blocks: {commands!r}"
        )

    def test_does_not_draw_charts_from_an_empty_store(self, rendered):
        if store_has_data():
            pytest.skip("artefacts/runs is populated, so the render branch is active")
        assert not rendered.tabs
        source = DASHBOARD.read_text(encoding="utf-8")
        assert "st.stop()" in source, (
            "an empty store must stop the render, not fall through to empty charts"
        )


class TestPopulatedStore:
    """The branch that needs artefacts on disk."""

    def test_renders_every_tab(self, rendered):
        if not store_has_data():
            pytest.skip("artefacts/runs is empty; run `make run-all` to exercise this")
        from tradeforge.interfaces.dashboard import TABS

        assert len(rendered.tabs) == len(TABS)


class TestNoDeprecatedStreamlitApi:
    def test_no_use_container_width(self):
        """Streamlit announced its removal for 2025-12-31.

        A deprecation that has already passed its removal date is a dashboard
        that breaks on the next upgrade, not a style preference.
        """
        source = DASHBOARD.read_text(encoding="utf-8")
        assert "use_container_width" not in source, (
            "use_container_width was removed in favour of width='stretch'"
        )
