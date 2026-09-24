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


class TestExperimentReport:
    """`experiment_report` and `ml_report` were unreachable for a while.

    Both were written, both looked finished, and nothing called them - the CLI
    only ever built execution reports. A report generator that no command can
    reach is a report that will be stale the first time someone changes the
    dataclass behind it. These tests keep both on the path.
    """

    def test_renders_cells_comparisons_and_the_seed_list(self, report_dir, configs):
        from tradeforge.application.harness import ExecutionHarness
        from tradeforge.research import build_experiment_specs, run_experiment

        harness = ExecutionHarness(configs)
        spec = build_experiment_specs(configs)["B_passive_vs_aggressive"]
        result = run_experiment(harness, spec, seeds=[20260908, 20260909], n_resamples=200)

        builder = HtmlReportBuilder(report_dir)
        out = builder.experiment_report(result, environment={"git_commit": "abc1234"})

        source = out.path.read_text(encoding="utf-8")
        assert not re.findall(r'(?:src|href)="(?!#)([^"]+)"', source)
        body = _body(source)
        assert "Paired comparisons" in body
        assert "abc1234" in body
        # The seed list must be visible: a comparison over one session is not
        # the same claim as one over ten.
        assert "20260908" in body and "20260909" in body

    def test_comparison_table_states_that_excluding_zero_is_not_a_verdict(
        self, report_dir, configs
    ):
        from tradeforge.application.harness import ExecutionHarness
        from tradeforge.research import build_experiment_specs, run_experiment

        harness = ExecutionHarness(configs)
        spec = build_experiment_specs(configs)["B_passive_vs_aggressive"]
        result = run_experiment(harness, spec, seeds=[20260908, 20260909], n_resamples=200)
        out = HtmlReportBuilder(report_dir).experiment_report(result)
        body = _body(out.path.read_text(encoding="utf-8"))
        assert "not a verdict" in body
        assert "Holm-Bonferroni" in body


class TestMlReport:
    def test_renders_the_lift_interval_and_both_leakage_checks(self, report_dir, configs):
        from tradeforge.data.registry import create_adapter
        from tradeforge.ml import (
            FillDatasetBuilder,
            MlConfig,
            SampleConfig,
            run_fill_probability_experiment,
        )

        ml_config = MlConfig.from_dict(configs)
        options = dict(configs["market_data"]["source"]["options"])
        # Enough samples for the purged split to be usable. At 8,000 events the
        # embargo leaves the validation block with two samples and
        # `assert_splits_usable` correctly refuses - which is the guard doing its
        # job, not a reason to weaken the assertion here.
        options["n_events"] = 60_000
        source = create_adapter(configs["market_data"]["source"]["adapter"], options)
        dataset = FillDatasetBuilder(
            symbol="SYNTH",
            config=SampleConfig(
                sample_interval_ns=ml_config.sample_interval_ns,
                horizon_ns=ml_config.horizon_ns,
            ),
        )
        dataset.run(source.events())
        result = run_fill_probability_experiment(
            dataset.samples, config=ml_config, dataset_report=dataset.report.to_dict()
        )

        out = HtmlReportBuilder(report_dir).ml_report(result)
        source_html = out.path.read_text(encoding="utf-8")
        assert not re.findall(r'(?:src|href)="(?!#)([^"]+)"', source_html)
        body = _body(source_html)
        assert "interval excludes zero" in body
        assert "Label-shuffle canary" in body
        assert "Feature leak screen" in body
        # The two checks catch different failures and the report must say so.
        assert "does <em>not</em> detect" in source_html
