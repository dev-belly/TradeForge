"""The Streamlit dashboard.

This suite exists because `make dashboard` was **completely broken** and nothing
noticed. `streamlit run <file>` executes the file as a script rather than as a
module in the package, so every relative import failed with "attempted relative
import with no known parent package". Streamlit renders that ImportError into the
page while the HTTP status stays 200, so a `curl` check reports success.

`streamlit.testing.v1.AppTest` runs the script the same way the server does, so
it reproduces the failure without a browser or a running server.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

streamlit = pytest.importorskip("streamlit", reason="the dashboard needs streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

DASHBOARD = Path(__file__).resolve().parents[2] / "src/tradeforge/interfaces/dashboard.py"


@pytest.fixture(scope="module")
def rendered(project_root) -> AppTest:
    """The dashboard, run the way the server runs it.

    Module-scoped because a cold render imports pandas and streamlit; doing it
    once keeps the suite quick.
    """
    app = AppTest.from_file(str(DASHBOARD), default_timeout=120)
    app.run()
    return app


class TestItRuns:
    def test_renders_without_raising(self, rendered):
        """The load-bearing assertion.

        Before the fix this reported an ImportError from the first relative
        import, which is what a user saw when they ran `make dashboard`.
        """
        assert not rendered.exception, "the dashboard raised while rendering:\n" + "\n".join(
            str(e.value) for e in rendered.exception
        )

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


class TestContent:
    def test_has_the_expected_title(self, rendered):
        assert [t.value for t in rendered.title] == ["TradeForge"]

    def test_declares_the_expected_tabs(self, rendered):
        assert len(rendered.tabs) == len(_declared_tabs())

    def test_states_the_data_is_synthetic(self, rendered):
        """Every figure on the page is from the generator, and the page says so.

        A screenshot of a cost table without this caption is a screenshot that
        gets quoted as if it came from a real venue.
        """
        source = DASHBOARD.read_text(encoding="utf-8")
        assert "SYNTHETIC" in source
        assert source.count("PROVENANCE_CAPTION") >= 5, (
            "every data tab should carry the provenance caption"
        )

    def test_does_not_render_an_empty_chart_when_the_store_is_empty(self):
        """An empty store must be stated, not drawn as empty axes.

        An empty chart reads as "the strategies performed identically", which is
        a different and much worse claim than "nothing ran".
        """
        source = DASHBOARD.read_text(encoding="utf-8")
        assert "st.stop()" in source
        assert "not evidence that the strategies performed" in source


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


def _declared_tabs() -> tuple[str, ...]:
    from tradeforge.interfaces.dashboard import TABS

    return tuple(TABS)
