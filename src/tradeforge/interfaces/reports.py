"""Self-contained HTML reports.

No JavaScript framework, no CDN, no build step. The output is a single file that
opens from disk, renders offline, and can be attached to a review. A report that
needs a network connection to render is a report that will one day fail to
render in front of the person who needed it.

Every report leads with its provenance and caveats, before any number. That
ordering is the point: a cost figure without its queue mode, latency basis and
counterfactual mode is not interpretable, and burying those in a footnote is how
they get ignored.
"""

from __future__ import annotations

import html
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CSS = """
:root {
  --bg: #fbfbfd; --panel: #ffffff; --ink: #1c1c22; --muted: #5b5b68;
  --line: #e3e3ea; --accent: #2b5cd9; --warn: #a8610a; --warn-bg: #fdf6e8;
  --good: #1f7a4d; --bad: #b3261e;
}
* { box-sizing: border-box; }
body { margin: 0; padding: 2.5rem 1.5rem; background: var(--bg); color: var(--ink);
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }
main { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 1.7rem; margin: 0 0 .3rem; letter-spacing: -.01em; }
h2 { font-size: 1.15rem; margin: 2.2rem 0 .7rem; padding-bottom: .35rem;
  border-bottom: 1px solid var(--line); }
h3 { font-size: 1rem; margin: 1.4rem 0 .5rem; color: var(--muted); font-weight: 600; }
p.lede { color: var(--muted); margin: 0 0 1.6rem; }
table { border-collapse: collapse; width: 100%; background: var(--panel);
  border: 1px solid var(--line); border-radius: 8px; overflow: hidden; font-size: 14px; }
th, td { padding: .5rem .75rem; text-align: left; border-bottom: 1px solid var(--line); }
th { background: #f4f4f8; font-weight: 600; font-size: 13px;
  text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
tr:last-child td { border-bottom: none; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.pos { color: var(--bad); } .neg { color: var(--good); }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  padding: 1rem 1.25rem; margin: 0 0 1rem; }
.caveat { background: var(--warn-bg); border: 1px solid #f0dcb8; border-left: 4px solid var(--warn);
  border-radius: 6px; padding: .9rem 1.1rem; margin: 0 0 .6rem; color: #4a3208; font-size: 14px; }
.prov { display: grid; grid-template-columns: max-content 1fr; gap: .3rem 1.2rem;
  font-size: 14px; }
.prov dt { color: var(--muted); } .prov dd { margin: 0; font-variant-numeric: tabular-nums; }
.badge { display: inline-block; padding: .12rem .5rem; border-radius: 999px; font-size: 12px;
  font-weight: 600; border: 1px solid currentColor; }
.badge.est { color: var(--warn); } .badge.exact { color: var(--good); }
.note { color: var(--muted); font-size: 13px; margin: .5rem 0 0; }
code { background: #f0f0f5; padding: .1rem .3rem; border-radius: 4px; font-size: 13px; }
footer { margin-top: 3rem; padding-top: 1rem; border-top: 1px solid var(--line);
  color: var(--muted); font-size: 13px; }
"""


def _escape(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        if value != value:
            return "n/a"
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return html.escape(str(value))


def _numeric_class(value: Any) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return ""
    if value > 0:
        return "pos"
    if value < 0:
        return "neg"
    return ""


def _table(
    headers: Sequence[str], rows: Iterable[Sequence[Any]], numeric: Sequence[int] = ()
) -> str:
    numeric_set = set(numeric)
    head = "".join(
        f'<th class="{"num" if i in numeric_set else ""}">{html.escape(h)}</th>'
        for i, h in enumerate(headers)
    )
    body_rows = []
    for row in rows:
        cells = []
        for i, cell in enumerate(row):
            classes = []
            if i in numeric_set:
                classes.append("num")
                sign = _numeric_class(cell)
                if sign:
                    classes.append(sign)
            cells.append(f'<td class="{" ".join(classes)}">{_escape(cell)}</td>')
        body_rows.append(f"<tr>{''.join(cells)}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


@dataclass(frozen=True, slots=True)
class HtmlReport:
    """A rendered report plus the data it was rendered from."""

    path: Path
    title: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"path": str(self.path), "title": self.title, "payload": self.payload}


class HtmlReportBuilder:
    """Builds the single-file HTML reports."""

    def __init__(self, output_dir: Path | str) -> None:
        self._dir = Path(output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def output_dir(self) -> Path:
        return self._dir

    # ------------------------------------------------------------- documents

    def execution_report(
        self, report: Any, *, run_id: str, provenance: dict[str, Any]
    ) -> HtmlReport:
        """One execution: headline, benchmarks, attribution, markouts, caveats."""
        metrics = report.metrics
        attribution = report.attribution
        benchmarks = report.benchmarks

        queue_mode = str(provenance.get("queue_mode", "UNKNOWN"))
        badge_class = "exact" if queue_mode.upper() == "EXACT" else "est"

        sections: list[str] = []
        sections.append(
            "<h1>Execution report</h1>"
            f'<p class="lede">{html.escape(run_id)} &middot; '
            f"{html.escape(metrics.policy)} &middot; {html.escape(metrics.side.value)} "
            f"&middot; queue position "
            f'<span class="badge {badge_class}">{html.escape(queue_mode)}</span></p>'
        )
        sections.append(self._caveats(report.caveats))
        sections.append("<h2>Provenance</h2>")
        sections.append(self._provenance(provenance))
        sections.append("<h2>Headline</h2>")
        sections.append(self._headline(report))
        sections.append("<h2>Benchmarks</h2>")
        sections.append(self._benchmarks(benchmarks))
        sections.append("<h2>Cost attribution</h2>")
        sections.append(self._attribution(attribution))
        sections.append("<h2>Markouts</h2>")
        sections.append(self._markouts(report.markouts))

        payload = report.to_dict()
        path = self._write(f"execution-{run_id}.html", "\n".join(sections))
        return HtmlReport(path=path, title=f"Execution report - {run_id}", payload=payload)

    def experiment_report(
        self, result: Any, *, environment: dict[str, Any] | None = None
    ) -> HtmlReport:
        """One experiment: per-cell aggregates, intervals, paired comparisons."""
        sections: list[str] = [
            f"<h1>{html.escape(result.name)}</h1>",
            f'<p class="lede">{html.escape(result.description)}</p>',
            self._caveats(result.caveats),
        ]
        if environment:
            sections.append("<h2>Environment</h2>")
            sections.append(self._provenance(environment))

        sections.append(self._cell_section(result))
        if result.comparisons:
            sections.append(self._comparison_section(result))
        sections.append(f"<h2>Seeds ({len(result.seeds)})</h2>")
        sections.append(f"<p>{html.escape(', '.join(str(s) for s in result.seeds))}</p>")
        sections.append(
            "<footer>Generated by TradeForge. All runs use SYNTHETIC data. Replay is "
            "counterfactual: our orders did not alter the event stream.</footer>"
        )

        path = self._write(f"experiment-{result.name}.html", "\n".join(sections))
        return HtmlReport(path=path, title=result.name, payload=result.to_dict())

    def _cell_section(self, result: Any) -> str:
        table = _table(
            [
                "cell",
                "runs",
                "usable",
                "mean",
                "ci low",
                "ci high",
                "fill ratio",
                "participation",
                "maker share",
            ],
            [
                [
                    cell.label,
                    cell.n_runs,
                    cell.n_usable,
                    cell.ci.estimate,
                    cell.ci.lower,
                    cell.ci.upper,
                    cell.mean_fill_ratio,
                    cell.mean_participation_rate,
                    cell.mean_maker_ratio,
                ]
                for cell in result.cells
            ],
            numeric=(1, 2, 3, 4, 5, 6, 7, 8),
        )
        note = (
            '<p class="note">Intervals are resampled over independent sessions '
            "(block length 1). Within-session analyses use the configured block "
            "length, because consecutive executions are autocorrelated. "
            "&ldquo;usable&rdquo; counts the runs whose metric was measurable; a "
            "cell whose usable count is below its run count has missing "
            "benchmarks, not zero cost.</p>"
        )
        return f"<h2>Cells (metric: {html.escape(result.metric)})</h2>{table}{note}"

    def _comparison_section(self, result: Any) -> str:
        table = _table(
            [
                "cell",
                "vs baseline",
                "mean difference (bps)",
                "ci low",
                "ci high",
                "wins",
                "losses",
                "p",
                "holm p",
                "interval excludes zero",
            ],
            [
                [
                    c.label_a,
                    c.label_b,
                    c.mean_difference,
                    c.ci.lower,
                    c.ci.upper,
                    c.wins_a,
                    c.wins_b,
                    c.sign_test_p,
                    c.holm_adjusted_p,
                    c.significant,
                ]
                for c in result.comparisons
            ],
            numeric=(2, 3, 4, 5, 6, 7, 8),
        )
        note = (
            '<p class="note">Difference is cell minus baseline; negative is cheaper. '
            "The p-values are corrected across the whole family with Holm-Bonferroni, "
            "and the raw values are shown so the cost of searching is visible. "
            "&ldquo;Excludes zero&rdquo; is a statement about the interval, not a "
            "verdict, and it is not a statement about which algorithm is better.</p>"
        )
        return f"<h2>Paired comparisons</h2>{table}{note}"

    def ml_report(self, result: Any) -> HtmlReport:
        """Fill-probability baselines with their leakage checks."""
        sections: list[str] = [
            "<h1>Fill-probability baselines</h1>",
            '<p class="lede">Do microstructure features carry any signal about '
            "whether a passive order fills within the horizon?</p>",
            self._caveats(result.notes),
            "<h2>Dataset</h2>",
            self._provenance(result.dataset),
            "<h2>Split</h2>",
            self._provenance(result.split.to_dict()),
        ]

        for label, reports in (
            ("Validation", result.validation_reports),
            ("Test (scored once, after model selection)", result.test_reports),
        ):
            sections.append(f"<h2>{html.escape(label)}</h2>")
            sections.append(
                _table(
                    ["model", "n", "base rate", "accuracy", "auc", "brier", "lift over base rate"],
                    [
                        [
                            r.model,
                            r.n,
                            r.base_rate,
                            r.accuracy,
                            r.auc,
                            r.brier,
                            r.lift_over_base_rate,
                        ]
                        for r in reports
                    ],
                    numeric=(1, 2, 3, 4, 5, 6),
                )
            )
            sections.append(
                '<p class="note">Accuracy is shown next to the base rate on purpose: '
                "on an imbalanced target, a model can look accurate while adding "
                "nothing.</p>"
            )

        sections.append("<h2>Leakage checks</h2>")
        sections.append("<h3>Label-shuffle canary</h3>")
        sections.append(
            _table(
                ["model", "auc on shuffled labels", "tolerance", "applicable", "passed"],
                [
                    [c.model, c.auc_on_shuffled_labels, c.tolerance, c.applicable, c.passed]
                    for c in result.canaries
                ],
                numeric=(1, 2),
            )
        )
        sections.append(
            '<p class="note">This canary detects fixed rules and preprocessing that '
            "carries label information from train to test. It does <em>not</em> detect "
            "a raw feature encoding the label &mdash; that is the screen below.</p>"
        )
        sections.append("<h3>Feature leak screen</h3>")
        flagged = [f for f in result.feature_screen if f.suspicious]
        sections.append(
            _table(
                ["feature", "univariate auc", "flagged"],
                [[f.feature, f.auc, f.suspicious] for f in result.feature_screen],
                numeric=(1,),
            )
        )
        if flagged:
            sections.append(
                '<div class="caveat">Flagged features: '
                f"{html.escape(', '.join(f.feature for f in flagged))}. Treat these "
                "metrics as unverified until the leak is explained.</div>"
            )
        else:
            sections.append('<p class="note">No feature ranked the label above 0.90 AUC.</p>')

        sections.append(
            "<footer>Generated by TradeForge. Synthetic data only; these numbers "
            "describe whether the features carry signal on a deterministic "
            "generator.</footer>"
        )
        path = self._write("ml-fill-probability.html", "\n".join(sections))
        return HtmlReport(path=path, title="Fill-probability baselines", payload=result.to_dict())

    def index(
        self, reports: Sequence[HtmlReport], *, title: str = "TradeForge reports"
    ) -> HtmlReport:
        rows = "".join(
            f'<li><a href="{html.escape(r.path.name)}">{html.escape(r.title)}</a></li>'
            for r in reports
        )
        body = (
            f"<h1>{html.escape(title)}</h1>"
            f'<p class="lede">{len(reports)} report(s).</p>'
            f"<ul>{rows}</ul>"
            "<footer>All results use SYNTHETIC data unless a report states otherwise. "
            "Each report carries its own provenance and caveats.</footer>"
        )
        path = self._write("index.html", body)
        return HtmlReport(path=path, title=title, payload={"n_reports": len(reports)})

    # -------------------------------------------------------------- sections

    def _caveats(self, caveats: Sequence[str]) -> str:
        if not caveats:
            return ""
        blocks = "".join(f'<div class="caveat">{html.escape(c)}</div>' for c in caveats)
        return f"<h2>Caveats</h2>{blocks}"

    def _provenance(self, provenance: dict[str, Any]) -> str:
        items = []
        for key, value in provenance.items():
            if isinstance(value, (dict, list)):
                rendered = html.escape(json.dumps(value, default=str, sort_keys=True))
            else:
                rendered = _escape(value)
            items.append(f"<dt>{html.escape(str(key))}</dt><dd>{rendered}</dd>")
        return f'<dl class="prov">{"".join(items)}</dl>'

    def _headline(self, report: Any) -> str:
        headline = report.headline
        numeric_keys = {
            "fill_ratio",
            "participation_rate",
            "cost_vs_arrival_bps",
            "cost_vs_vwap_bps",
            "cost_vs_twap_bps",
            "implementation_shortfall_bps",
            "maker_fill_ratio",
        }
        rows = []
        for key, value in headline.items():
            if key in numeric_keys and isinstance(value, (int, float)):
                rows.append([key, f"{value:,.6f}"])
            else:
                rows.append([key, value])
        return _table(["field", "value"], rows)

    def _benchmarks(self, benchmarks: Any) -> str:
        table = _table(
            ["benchmark", "ticks", "basis"],
            [
                ["arrival mid", benchmarks.arrival_mid_ticks, "first mid at/after window start"],
                [
                    "interval VWAP",
                    benchmarks.interval_vwap_ticks,
                    f"volume weighted over {benchmarks.n_trade_prints} prints",
                ],
                ["interval TWAP", benchmarks.interval_twap_ticks, "time weighted mid, trapezoidal"],
                ["interval mid", benchmarks.interval_mid_ticks, "unweighted mean of observed mids"],
                ["terminal mid", benchmarks.terminal_mid_ticks, "last mid inside the window"],
            ],
            numeric=(1,),
        )
        coverage = benchmarks.coverage()
        note = (
            '<p class="note">Window '
            f"{benchmarks.window_ns / 1e9:,.0f}s &middot; "
            f"{coverage['n_mid_observations']:,} mid observations &middot; "
            f"{coverage['n_trade_prints']:,} trade prints &middot; "
            f"{coverage['window_volume_base']:,} base units traded. "
            "A benchmark resting on few observations is weak, and the count is "
            "reported so that is visible.</p>"
        )
        return table + note

    def _attribution(self, attribution: Any) -> str:
        rows = [
            [
                "implementation shortfall (filled)",
                attribution.is_filled_bps,
                "signed against the arrival price, per filled share",
            ],
            ["spread cost", attribution.spread_cost_bps, "negative for maker fills, which earn it"],
            ["fees", attribution.fees_bps, "negative when rebates exceed fees"],
            ["timing", attribution.timing_bps, "market drift between arrival and fills"],
            [
                "residual (unexplained)",
                attribution.residual_impact_bps,
                "approximation error plus unmodelled impact - NOT a measured impact",
            ],
            [
                "opportunity cost",
                attribution.opportunity_cost_bps,
                "Perold charge on the unfilled quantity",
            ],
            [
                "implementation shortfall (total)",
                attribution.is_total_bps,
                "per requested share: filled leg plus opportunity cost",
            ],
        ]
        table = _table(["component", "bps", "meaning"], rows, numeric=(1,))
        share = attribution.residual_share
        if share is not None:
            verdict = (
                "the decomposition is not describing this execution well"
                if abs(share) > 0.5
                else "the residual is small, so the components carry the story"
            )
            table += (
                f'<p class="note">Residual share of the filled-leg cost: '
                f"{share:,.2%} &mdash; {verdict}.</p>"
            )
        return table

    def _markouts(self, markouts: Sequence[Any]) -> str:
        table = _table(
            [
                "horizon (s)",
                "measurable",
                "of fills",
                "coverage",
                "volume weighted (bps)",
                "median (bps)",
                "p25",
                "p75",
            ],
            [
                [
                    m.horizon_ns / 1e9,
                    m.n_measurable,
                    m.n_fills,
                    m.coverage,
                    m.volume_weighted_bps,
                    m.median_bps,
                    m.p25_bps,
                    m.p75_bps,
                ]
                for m in markouts
            ],
            numeric=(0, 1, 2, 3, 4, 5, 6, 7),
        )
        return table + (
            '<p class="note">Positive is adverse: the mid moved against us after the '
            "fill. A short-horizon negative turning into a long-horizon positive is "
            "the signature of adverse selection, and it is invisible in an average "
            "fill price.</p>"
        )

    # ----------------------------------------------------------------- write

    def _write(self, filename: str, body: str) -> Path:
        document = (
            "<!DOCTYPE html>\n"
            '<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>TradeForge - {html.escape(filename)}</title>"
            f"<style>{CSS}</style></head><body><main>{body}</main></body></html>\n"
        )
        path = self._dir / filename
        path.write_text(document, encoding="utf-8")
        return path
