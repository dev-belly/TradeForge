"""The examples and the docs, checked against the code they describe.

Two things a reader is most likely to try first, and neither had any coverage:

* the five `examples/` scripts, which the README presents as the way in;
* the commands, make targets and imports named in the guides.

A documented command that no longer exists is worse than no documentation: it
costs the reader the time to discover the docs are wrong before they can start
looking for the real answer. The checks here are cheap and catch exactly that.

**What is not checked.** The prose, the numbers in the tables, and the Python
snippets in the guides are not executed. Verifying every snippet would mean
maintaining a doctest harness for code that is deliberately illustrative; the
`make demo` table is checked because it is the repository's headline claim and
because it is cheap to check.
"""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = sorted((ROOT / "examples").glob("*.py"))
GUIDES = sorted((ROOT / "docs/guides").glob("*.md"))
DOCS = [ROOT / "README.md", *GUIDES]

#: Prose that happens to contain the word "make", not a target reference.
_PROSE = {"the", "a", "it", "them", "sure", "this", "that", "no"}


def code_spans(path: Path) -> list[str]:
    """Fenced blocks and inline code spans - where a command reference lives.

    Restricting to code spans matters: `docs/guides/execution.md` contains the
    sentence "would make latency free", and a whole-file search reads that as a
    reference to a `make latency` target that has never existed.
    """
    text = path.read_text(encoding="utf-8")
    return re.findall(r"```.*?```", text, re.S) + re.findall(r"`[^`\n]+`", text)


def makefile_targets() -> set[str]:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    return set(re.findall(r"^([a-z][a-z0-9-]*):", makefile, re.M))


@pytest.fixture(scope="module")
def example_runs() -> dict[str, subprocess.CompletedProcess]:
    """Run each example once and share the result.

    Running them per-assertion doubled the cost of the slowest test file for no
    extra coverage - `04_paired_comparison.py` alone takes about 25 seconds.
    """
    runs = {}
    for example in EXAMPLES:
        runs[example.name] = subprocess.run(
            [sys.executable, str(example)],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
            timeout=240,
            check=False,
        )
    return runs


class TestExamples:
    """The scripts the README points a new reader at."""

    def test_there_are_examples_to_run(self):
        assert EXAMPLES, "examples/ is empty"

    def test_every_example_is_numbered_and_named(self):
        """The numbering is the reading order, and the README refers to it."""
        for path in EXAMPLES:
            assert re.match(r"^\d{2}_[a-z0-9_]+\.py$", path.name), (
                f"{path.name} does not follow NN_name.py; the README lists them in that order"
            )

    def test_the_readme_lists_every_example(self):
        readme = (ROOT / "examples/README.md").read_text(encoding="utf-8")
        unlisted = [p.name for p in EXAMPLES if p.name not in readme]
        assert not unlisted, f"examples/README.md does not mention {unlisted}"

    def test_every_example_exits_zero(self, example_runs):
        """Run the way the guides say to run it.

        An example that does not run is a broken front door, and nothing else in
        the suite would notice.
        """
        failures = {name: result for name, result in example_runs.items() if result.returncode != 0}
        assert not failures, "\n\n".join(
            f"{name} exited {r.returncode}\n--- stderr ---\n{r.stderr[-1500:]}"
            for name, r in failures.items()
        )

    def test_every_example_prints_something(self, example_runs):
        """An example that runs silently teaches nothing."""
        quiet = {
            name: len(result.stdout.strip())
            for name, result in example_runs.items()
            if len(result.stdout.strip()) <= 100
        }
        assert not quiet, f"these examples printed almost nothing: {quiet}"

    def test_no_example_prints_an_absolute_home_path(self, example_runs):
        """Examples must run for anyone, and must not leak a local layout.

        A path like `/Users/someone/...` in the output is both a portability
        smell and a small privacy leak in a public repository.
        """
        offenders = {
            name: line
            for name, result in example_runs.items()
            for line in result.stdout.splitlines()
            if re.search(r"/(?:Users|home)/", line)
        }
        assert not offenders, f"examples printed absolute home paths: {offenders}"


class TestDocumentedCommands:
    """Every command named in a code span must exist."""

    def test_every_make_target_exists(self):
        defined = makefile_targets()
        missing: dict[str, list[str]] = {}
        for doc in DOCS:
            for span in code_spans(doc):
                for match in re.finditer(r"\bmake\s+([a-z][a-z0-9-]*)", span):
                    target = match.group(1)
                    if target in _PROSE or target in defined:
                        continue
                    missing.setdefault(target, []).append(doc.name)
        assert not missing, "these docs reference make targets that do not exist: " + ", ".join(
            f"{t} ({', '.join(sorted(set(w)))})" for t, w in sorted(missing.items())
        )

    def test_every_documented_import_resolves(self):
        """A guide that tells the reader to import a symbol must be right."""
        problems: list[str] = []
        for doc in DOCS:
            for span in code_spans(doc):
                for match in re.finditer(r"from (tradeforge[\w.]*) import ([\w, ]+)", span):
                    module_name = match.group(1)
                    try:
                        module = importlib.import_module(module_name)
                    except ImportError as exc:
                        problems.append(f"{doc.name}: cannot import {module_name} ({exc})")
                        continue
                    for symbol in (s.strip() for s in match.group(2).split(",")):
                        if symbol and not hasattr(module, symbol):
                            problems.append(f"{doc.name}: {module_name} has no {symbol!r}")
        assert not problems, "\n".join(problems)

    def test_every_documented_module_is_runnable(self):
        problems: list[str] = []
        for doc in DOCS:
            for span in code_spans(doc):
                for match in re.finditer(r"python -m (tradeforge[\w.]*)", span):
                    module_name = match.group(1)
                    try:
                        importlib.import_module(module_name)
                    except ImportError as exc:
                        problems.append(f"{doc.name}: python -m {module_name} ({exc})")
        assert not problems, "\n".join(problems)


class TestTheHeadlineClaim:
    """`getting-started.md` quotes the `make demo` table as the repository's point.

    The guide's argument is that every strategy is about 7 bps cheaper than the
    arrival price and about 3 bps more expensive than the interval VWAP, and that
    both are correct because the market drifted. That argument rests on the
    numbers, so a drift in the generator would make the guide misleading rather
    than merely stale.
    """

    @staticmethod
    def _demo_rows() -> dict[tuple[str, str], dict[str, float]]:
        from tradeforge.application.harness import ExecutionHarness, RunRequest
        from tradeforge.infrastructure.config import load_configs, validate_required

        configs = load_configs(ROOT / "configs")
        validate_required(configs)
        harness = ExecutionHarness(configs)
        rows = {}
        for policy in ("twap", "vwap", "pov", "is_baseline"):
            for style in ("passive", "aggressive"):
                context = harness.run(RunRequest(policy=policy, style=style, seed=20260908))
                assert context.tca is not None
                metrics = context.tca.metrics
                rows[(policy, style)] = {
                    "fill_ratio": metrics.fill_ratio,
                    "maker_fill_ratio": metrics.maker_fill_ratio,
                    "vs_arrival_bps": metrics.implementation_shortfall_bps,
                }
        return rows

    def test_the_quoted_numbers_still_hold(self):
        rows = self._demo_rows()
        quoted = {
            ("twap", "passive"): (1.0, 0.963, -7.9369),
            ("twap", "aggressive"): (1.0, 0.051, -6.9336),
        }
        for key, (fill, maker, is_bps) in quoted.items():
            row = rows[key]
            assert row["fill_ratio"] == pytest.approx(fill, abs=5e-4), key
            assert row["maker_fill_ratio"] == pytest.approx(maker, abs=5e-4), key
            assert row["vs_arrival_bps"] == pytest.approx(is_bps, abs=5e-4), key

    def test_the_guide_calls_its_table_an_excerpt(self):
        """The guide shows two of eight rows and omits the participation column.

        Presenting an excerpt as "prints:" invites the reader to think their
        output is wrong when it has eight rows and an extra column.
        """
        guide = (ROOT / "docs/guides/getting-started.md").read_text(encoding="utf-8")

        # Locate the quoted table by its header, then read the text above the fence.
        header = guide.index("vs_arrival_bps")
        block_start = guide.rindex("```", 0, header)
        paragraph = guide[max(0, block_start - 400) : block_start]
        assert re.search(r"excerpt|first two|dropped for width", paragraph, re.I), (
            "the quoted demo table is two of eight rows with a column removed; the "
            "paragraph introducing it should say so, or a reader whose output has "
            "eight rows will think something is wrong. Paragraph was: "
            f"{paragraph.strip()[:200]!r}"
        )

    def test_the_benchmark_choice_still_flips_the_sign(self):
        """The repository's headline finding, asserted rather than described.

        If a change ever makes the two benchmarks agree, the README's first
        honest finding becomes false and this fails.
        """
        from tradeforge.application.harness import ExecutionHarness, RunRequest
        from tradeforge.infrastructure.config import load_configs, validate_required

        configs = load_configs(ROOT / "configs")
        validate_required(configs)
        harness = ExecutionHarness(configs)
        context = harness.run(RunRequest(policy="twap", style="passive", seed=20260908))
        assert context.tca is not None

        metrics = context.tca.metrics
        arrival = metrics.cost_vs_arrival_bps
        vwap = metrics.cost_vs_vwap_bps
        assert arrival < 0 < vwap, (
            "the sign flip between benchmarks is the repository's headline claim; "
            f"arrival={arrival:.4f} vwap={vwap:.4f}"
        )
