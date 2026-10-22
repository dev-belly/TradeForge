"""Default assets work without a checkout; explicit paths remain strict."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tradeforge.domain.exceptions import ConfigurationError
from tradeforge.infrastructure.config import load_configs
from tradeforge.infrastructure.resources import PACKAGE_RESOURCE_ROOT

ROOT = Path(__file__).resolve().parents[2]


def cli(directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "tradeforge.interfaces.cli", *arguments],
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


@pytest.mark.parametrize("name,pattern", [("configs", "*.yaml"), ("sql", "*.sql")])
def test_installed_defaults_match_editable_resources(name: str, pattern: str) -> None:
    editable = {path.name: path.read_bytes() for path in (ROOT / name).glob(pattern)}
    packaged = {
        path.name: path.read_bytes() for path in (PACKAGE_RESOURCE_ROOT / name).glob(pattern)
    }
    assert editable
    assert packaged == editable


@pytest.mark.parametrize(
    "arguments,expected",
    [(("doctor",), "backend:"), (("db", "list"), "07_data_quality_summary")],
)
def test_cli_defaults_work_outside_checkout(
    tmp_path: Path, arguments: tuple[str, ...], expected: str
) -> None:
    result = cli(tmp_path, *arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    assert expected in result.stdout
    if arguments == ("doctor",):
        assert "market_data: ok" in result.stdout


def test_python_defaults_follow_working_directory_changes(tmp_path: Path, monkeypatch) -> None:
    original = load_configs(ROOT / "configs")
    monkeypatch.chdir(tmp_path)
    assert load_configs() == original
    with pytest.raises(ConfigurationError, match="config directory not found"):
        load_configs("configs")
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/book.yaml").write_text("resource_probe: 123\n", encoding="utf-8")
    assert load_configs() == {"book": {"resource_probe": 123}}
    second = tmp_path / "second"
    second.mkdir()
    monkeypatch.chdir(second)
    assert load_configs() == original


def test_cli_defaults_follow_chdir_after_import(tmp_path: Path, monkeypatch) -> None:
    from typer.testing import CliRunner

    from tradeforge.interfaces.cli import app

    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(app, ["doctor"]).exit_code == 0
    assert "07_data_quality_summary" in runner.invoke(app, ["db", "list"]).stdout
    (tmp_path / "sql").mkdir()
    (tmp_path / "sql/custom.sql").write_text(
        "-- Question: Local after import\nSELECT 1;\n", encoding="utf-8"
    )
    local = runner.invoke(app, ["db", "list"])
    assert local.exit_code == 0
    assert "Local after import" in local.stdout
    assert "07_data_quality_summary" not in local.stdout


def test_existing_local_configs_do_not_fall_back_when_incomplete(tmp_path: Path) -> None:
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/book.yaml").write_text("mode: l2_mbp\n", encoding="utf-8")
    result = cli(tmp_path, "doctor")
    assert result.returncode != 0
    assert "missing required configs" in result.stdout + result.stderr


def test_local_sql_directory_takes_priority(tmp_path: Path) -> None:
    (tmp_path / "sql").mkdir()
    (tmp_path / "sql/custom.sql").write_text(
        "-- Question: Custom query\nSELECT 1;\n", encoding="utf-8"
    )
    result = cli(tmp_path, "db", "list")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Custom query" in result.stdout
    assert "01_execution_summary" not in result.stdout


@pytest.mark.parametrize(
    "arguments", [("doctor", "--configs", "configs"), ("db", "list", "--sql-dir", "sql")]
)
def test_explicit_missing_directories_never_use_defaults(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    result = cli(tmp_path, *arguments)
    assert result.returncode != 0
    assert arguments[-1] in result.stdout + result.stderr


@pytest.mark.parametrize("quantity", ["0", "-1", "nan", "inf", "-inf"])
def test_cli_rejects_invalid_parent_quantities(tmp_path: Path, quantity: str) -> None:
    result = cli(tmp_path, "run", "--quantity", quantity, "--json")
    assert result.returncode != 0
    assert "quantity" in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
    assert "requested_base" not in result.stdout


def test_cli_run_keeps_a_positive_quantity_override(tmp_path: Path) -> None:
    result = cli(tmp_path, "run", "--quantity", "100", "--json")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["metrics"]["requested_base"] == 100


def test_cli_reports_invalid_configured_quantity_without_a_traceback(tmp_path: Path) -> None:
    configs = tmp_path / "configs"
    shutil.copytree(ROOT / "configs", configs)
    path = configs / "execution.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    payload["parent_order"]["quantity_base"] = 0
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    result = cli(tmp_path, "run", "--json")
    assert result.returncode != 0
    assert "quantity_base must be a positive integer" in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
