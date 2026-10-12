"""The HTTP API.

Like the dashboard, this had no tests. The API is the surface a reviewer is most
likely to poke at first, and an endpoint that returns 500 with a stack trace is
a worse first impression than one that is missing.

Every endpoint is exercised, including the failure paths - a 422 that carries a
useful message is part of the contract, not an implementation detail.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="the API needs fastapi")
pytest.importorskip("httpx", reason="TestClient needs httpx")

from fastapi.testclient import TestClient

from tradeforge.interfaces.api import app
from tradeforge.interfaces.reports import HtmlReportBuilder


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


class TestHealth:
    def test_reports_the_backend_it_is_running(self, client):
        """Which engine produced a number is not decoration.

        A result from the Python reference and one from the compiled core are
        different claims about throughput, and the API says which one answered.
        """
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["engine_backend"] in {"python-reference", "cpp"}
        assert body["engine_detail"]


class TestReadOnlyEndpoints:
    def test_lists_the_experiment_grids(self, client):
        body = client.get("/configs/experiments").json()
        names = {e["name"] for e in body["experiments"]}
        assert "A_algorithm_comparison" in names
        for experiment in body["experiments"]:
            assert experiment["description"]

    def test_lists_the_data_adapters_with_their_declared_tier(self, client):
        """The declared tier is a claim about the source, and it is exposed so a
        caller can see it rather than inferring it from a result."""
        body = client.get("/datasets/adapters").json()
        assert body["adapters"]["synthetic"] == "L2_MBP"

    def test_lists_the_packaged_queries_with_their_questions(self, client):
        body = client.get("/queries").json()
        assert len(body["queries"]) == 7
        for query in body["queries"]:
            assert query["question"], f"{query['name']} has no stated question"

    def test_an_unknown_query_is_404_and_names_the_alternatives(self, client):
        """404 regardless of what the store holds.

        An earlier version checked for artefacts before validating the name, so
        this answered 200 with a "no artefacts" note on an empty store and 404 on
        a populated one - describing a nonexistent query as a query that found
        nothing, and changing the answer based on unrelated state.
        """
        response = client.get("/queries/99_nope")
        assert response.status_code == 404
        detail = response.json()["detail"]
        assert "99_nope" in detail
        assert "01_execution_summary" in detail

    def test_a_known_query_without_its_tables_says_which_are_missing(self, client):
        """A missing table is a state, not an error.

        DuckDB's CatalogException names the table but not that it is *expected*
        to be absent until something writes it, nor what writes it.
        """
        response = client.get("/queries/07_data_quality_summary")
        assert response.status_code == 200
        body = response.json()
        if body.get("rows"):
            pytest.skip("artefacts/runs is populated, so the query ran")
        assert "does not hold" in body["note"]
        assert "events" in body["note"]
        assert "make run-all" in body["note"]

    def test_the_store_status_is_reported_with_every_answer(self, client):
        """So a caller can tell "no rows" from "no data" without a second call."""
        body = client.get("/queries/01_execution_summary").json()
        assert "store" in body
        assert "present_tables" in body["store"]

    def test_an_unknown_report_is_404(self, client):
        response = client.get("/reports/does-not-exist")
        assert response.status_code == 404
        assert "does-not-exist" in response.json()["detail"]


class TestExecutionEndpoint:
    def test_runs_a_default_execution(self, client):
        response = client.post("/executions", json={})
        assert response.status_code == 200
        body = response.json()
        assert body["execution"]["policy"] == "twap"
        assert body["execution"]["completion_rate"] > 0

    def test_the_response_carries_provenance(self, client):
        """A cost number without its provenance is a number nobody can check."""
        body = client.post(
            "/executions", json={"policy": "pov", "style": "aggressive", "seed": 7}
        ).json()
        provenance = body["tca"]["provenance"]
        assert provenance["queue_mode"]
        assert provenance["latency_basis"]
        assert provenance["counterfactual_mode"] == "replay_approximation"

    def test_the_same_seed_reproduces_the_same_run(self, client):
        payload = {"policy": "vwap", "style": "passive", "seed": 4242}
        first = client.post("/executions", json=payload).json()
        second = client.post("/executions", json=payload).json()
        assert (
            first["execution"]["avg_fill_price_ticks"]
            == second["execution"]["avg_fill_price_ticks"]
        )

    def test_an_unknown_policy_is_422_with_a_readable_message(self, client):
        response = client.post("/executions", json={"policy": "nonsense"})
        assert response.status_code == 422
        assert "nonsense" in str(response.json()["detail"])

    def test_a_non_positive_quantity_is_rejected(self, client):
        response = client.post("/executions", json={"quantity_base": -5})
        assert response.status_code == 422


@pytest.fixture
def report_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "reports"
    root.mkdir()
    monkeypatch.setattr(app.state, "report_root", root.resolve())
    return root


class TestReportScope:
    def test_report_root_is_selected_by_server_startup_configuration(self, tmp_path):
        configured_root = tmp_path / "operator-reports"
        rendered = HtmlReportBuilder(configured_root).index([])
        code = (
            "import sys; "
            "from fastapi.testclient import TestClient; "
            "from tradeforge.interfaces.api import app; "
            "response = TestClient(app).get('/reports/index.html'); "
            "assert response.status_code == 200; "
            "sys.stdout.buffer.write(response.content)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            env={**os.environ, "TRADEFORGE_REPORT_ROOT": str(configured_root)},
            check=True,
            capture_output=True,
        )
        assert result.stdout.decode("utf-8") == rendered.path.read_text(encoding="utf-8")

    def test_serves_a_generated_report_from_the_configured_root(self, client, report_root):
        rendered = HtmlReportBuilder(report_root).index([])
        response = client.get(f"/reports/{rendered.path.name}")
        assert response.status_code == 200
        assert response.text == rendered.path.read_text(encoding="utf-8")
        assert response.headers["content-type"].startswith("text/html")

    def test_serves_relative_subdirectories(self, client, report_root):
        rendered = HtmlReportBuilder(report_root / "research" / "baseline").index([])
        response = client.get("/reports/index.html", params={"directory": "research/baseline"})
        assert response.status_code == 200
        assert response.text == rendered.path.read_text(encoding="utf-8")

    def test_caller_cannot_select_an_arbitrary_readable_directory(
        self, client, report_root, tmp_path
    ):
        private = tmp_path / "private"
        private.mkdir()
        (private / "secret.html").write_text("private document", encoding="utf-8")
        response = client.get("/reports/secret.html", params={"directory": str(private)})
        assert response.status_code == 400
        assert "private document" not in response.text

    @pytest.mark.parametrize(
        "directory",
        [
            "../private",
            "research/../../private",
            r"..\private",
            "/private",
            r"\private",
            r"C:\private",
            "C:private",
            r"\\server\share",
            "research:stream",
            "research\x00",
        ],
    )
    def test_rejects_traversal_and_absolute_paths_on_either_platform(
        self, client, report_root, directory
    ):
        response = client.get("/reports/index.html", params={"directory": directory})
        assert response.status_code == 400

    @pytest.mark.parametrize("name", [r"..\private\secret.html", "index.html:stream"])
    def test_report_name_cannot_contain_path_components(self, client, report_root, name):
        response = client.get(f"/reports/{name}")
        assert response.status_code == 400

    @pytest.mark.parametrize("directory_link", [False, True])
    def test_symlinks_cannot_escape_the_configured_root(
        self, client, report_root, tmp_path, directory_link
    ):
        private = tmp_path / "private"
        private.mkdir()
        (private / "secret.html").write_text("private document", encoding="utf-8")
        target = private if directory_link else private / "secret.html"
        link = report_root / ("linked" if directory_link else "secret.html")
        try:
            link.symlink_to(target, target_is_directory=directory_link)
        except OSError as error:
            if os.name == "nt" and error.winerror == 1314:
                pytest.skip("Windows does not allow this user to create symlinks")
            raise
        response = client.get(
            "/reports/secret.html", params={"directory": "linked" if directory_link else ""}
        )
        assert response.status_code == 400
        assert "private document" not in response.text

    def test_non_html_files_are_not_served(self, client, report_root):
        (report_root / "notes.txt").write_text("internal notes", encoding="utf-8")
        response = client.get("/reports/notes.txt")
        assert response.status_code == 404
        assert "internal notes" not in response.text


class TestOpenApi:
    def test_the_schema_is_served(self, client):
        """A published schema is what lets a caller avoid guessing."""
        schema = client.get("/openapi.json").json()
        assert set(schema["paths"]) >= {
            "/health",
            "/executions",
            "/queries",
            "/queries/{name}",
            "/reports/{name}",
            "/configs/experiments",
            "/datasets/adapters",
        }

    def test_every_operation_has_a_summary_or_description(self, client):
        schema = client.get("/openapi.json").json()
        undocumented = [
            f"{method.upper()} {path}"
            for path, operations in schema["paths"].items()
            for method, spec in operations.items()
            if not (spec.get("summary") or spec.get("description"))
        ]
        assert not undocumented, f"undocumented operations: {undocumented}"
