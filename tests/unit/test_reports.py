"""HTML report generation.

The reports make three claims that are worth testing rather than asserting in a
docstring:

  * they are self-contained - no CDN, no external stylesheet, no script tag;
  * provenance and caveats come *before* the first number;
  * they do not contradict themselves - the queue-position badge at the top must
    agree with the headline table below it.

The third one was a real bug: the CLI passed the harness provenance instead of
the TCA provenance, so the badge read "UNKNOWN" while the table said
"APPROXIMATE".
"""

from __future__ import annotations

import html
import re
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from tradeforge.interfaces.reports import HtmlReportBuilder

pytest.importorskip("pandas", reason="reports are rendered from dataclass payloads")

#: Report output goes under the repository's gitignored `artifacts/` tree rather
#: than pytest's temporary directory. Some sandboxes deny writes outside the
#: workspace, and a test that cannot create its output directory fails for a
#: reason that has nothing to do with the code under test.
OUTPUT_ROOT = Path("artifacts/test-reports")


@pytest.fixture
def report_dir() -> Iterator[Path]:
    directory = OUTPUT_ROOT / uuid.uuid4().hex[:12]
    directory.mkdir(parents=True, exist_ok=True)
    try:
        yield directory
    finally:
        shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture(scope="module")
def report(configs):
    from tradeforge.application.harness import ExecutionHarness, RunRequest

    harness = ExecutionHarness(configs)
    context = harness.run(
        RunRequest(policy="twap", style="passive", seed=20260908, run_id="report-test")
    )
    assert context.tca is not None
    return context


@pytest.fixture
def rendered(report_dir, report):
    builder = HtmlReportBuilder(report_dir)
    return builder.execution_report(
        report.tca, run_id="report-test", provenance=report.tca.provenance
    )


def _body(source: str) -> str:
    """The document without its stylesheet, as plain text."""
    body = source.split("</style>", 1)[1]
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " | ", body)))


class TestSelfContained:
    def test_no_external_references(self, rendered):
        """A report that needs a network connection will one day fail to render."""
        source = rendered.path.read_text(encoding="utf-8")
        external = re.findall(r'(?:src|href)="(?!#)([^"]+)"', source)
        assert not external, f"the report references external resources: {external}"

    def test_no_script_tags(self, rendered):
        source = rendered.path.read_text(encoding="utf-8")
        assert "<script" not in source.lower()

    def test_styles_are_inlined(self, rendered):
        source = rendered.path.read_text(encoding="utf-8")
        assert "<style>" in source
        assert "var(--ink)" in source

    def test_is_a_complete_document(self, rendered):
        source = rendered.path.read_text(encoding="utf-8")
        assert source.startswith("<!DOCTYPE html>")
        assert "</html>" in source


class TestProvenanceFirst:
    def test_caveats_appear_before_any_cost_number(self, rendered):
        """The ordering is the point: a cost figure without its caveats is not
        interpretable, and a footnote is where caveats go to be ignored."""
        body = _body(rendered.path.read_text(encoding="utf-8"))
        caveat_at = body.find("Replay is counterfactual")
        assert caveat_at != -1, "the counterfactual caveat is missing"
        first_cost = body.find("cost_vs_arrival_bps")
        assert first_cost == -1 or caveat_at < first_cost

    def test_provenance_appears_before_the_headline(self, rendered):
        body = _body(rendered.path.read_text(encoding="utf-8"))
        provenance_at = body.find("config_fingerprint")
        headline_at = body.find("Headline")
        assert provenance_at != -1 and provenance_at < headline_at

    def test_all_three_caveats_are_present(self, rendered):
        body = _body(rendered.path.read_text(encoding="utf-8"))
        assert "counterfactual" in body
        assert "post-fill data" in body
        assert "ESTIMATE" in body


class TestNoSelfContradiction:
    def test_queue_badge_matches_the_headline_table(self, rendered, report):
        """The bug this pins: the badge read UNKNOWN while the table said
        APPROXIMATE, because the CLI passed the wrong provenance dict."""
        source = rendered.path.read_text(encoding="utf-8")
        badge = re.search(r'badge \w+">([^<]+)<', source)
        assert badge is not None, "no queue-position badge was rendered"
        assert badge.group(1) == report.tca.provenance["queue_mode"]

    def test_the_residual_is_labelled_unexplained(self, rendered):
        body = _body(rendered.path.read_text(encoding="utf-8"))
        assert "unexplained" in body
        assert "NOT a measured impact" in body

    def test_markout_sign_convention_is_stated(self, rendered):
        body = _body(rendered.path.read_text(encoding="utf-8"))
        assert "positive is adverse" in body.lower()


class TestIndex:
    def test_index_links_every_report(self, report_dir, report):
        builder = HtmlReportBuilder(report_dir)
        reports = [
            builder.execution_report(
                report.tca, run_id=f"run-{i}", provenance=report.tca.provenance
            )
            for i in range(3)
        ]
        index = builder.index(reports)
        source = index.path.read_text(encoding="utf-8")
        for item in reports:
            assert item.path.name in source

    def test_index_keeps_the_synthetic_disclaimer(self, report_dir, report):
        builder = HtmlReportBuilder(report_dir)
        index = builder.index(
            [builder.execution_report(report.tca, run_id="x", provenance=report.tca.provenance)]
        )
        source = index.path.read_text(encoding="utf-8")
        assert "SYNTHETIC" in source


class TestEscaping:
    def test_html_in_a_value_is_escaped(self, report_dir, report):
        """A provenance value containing markup must not become markup."""
        builder = HtmlReportBuilder(report_dir)
        poisoned = dict(report.tca.provenance)
        poisoned["dataset_name"] = "<script>alert(1)</script>"
        out = builder.execution_report(report.tca, run_id="escape", provenance=poisoned)
        source = out.path.read_text(encoding="utf-8")
        assert "<script>alert(1)</script>" not in source
        assert "&lt;script&gt;" in source
