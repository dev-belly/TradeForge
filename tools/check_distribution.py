"""Install a built wheel into a clean environment and exercise it outside the checkout."""

from __future__ import annotations

import argparse
import os
import subprocess
import tempfile
import venv
from pathlib import Path
from zipfile import ZipFile


def run(command: list[str], *, directory: Path, environment: dict[str, str]) -> str:
    result = subprocess.run(
        command,
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Command failed: {command}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def check_wheel_resources(wheel: Path, root: Path) -> None:
    with ZipFile(wheel) as archive:
        for name, pattern in (("configs", "*.yaml"), ("sql", "*.sql")):
            expected = {path.name: path.read_bytes() for path in (root / name).glob(pattern)}
            prefix = f"tradeforge/_resources/{name}/"
            actual = {
                member.removeprefix(prefix): archive.read(member)
                for member in archive.namelist()
                if member.startswith(prefix) and not member.endswith("/")
            }
            if not expected or actual != expected:
                raise RuntimeError(f"Wheel {name} resources differ from the editable defaults")


def check_installed(wheel: Path) -> None:
    environment = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME"):
        environment.pop(name, None)
    environment.update(PYTHONUTF8="1", PYTHONNOUSERSITE="1", COLUMNS="200")
    with tempfile.TemporaryDirectory(prefix="tradeforge-installed-") as temporary:
        directory = Path(temporary)
        virtualenv = directory / "environment"
        venv.create(virtualenv, with_pip=True)
        python = virtualenv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        outside = directory / "outside"
        outside.mkdir()
        run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", str(wheel)],
            directory=outside,
            environment=environment,
        )
        package = run(
            [str(python), "-c", "import tradeforge; print(tradeforge.__file__)"],
            directory=outside,
            environment=environment,
        ).strip()
        if not Path(package).resolve().is_relative_to(virtualenv.resolve()):
            raise RuntimeError(f"Imported source checkout instead of installed wheel: {package}")
        executable = virtualenv / (
            "Scripts/tradeforge.exe" if os.name == "nt" else "bin/tradeforge"
        )
        command = [str(executable)]
        doctor = run([*command, "doctor"], directory=outside, environment=environment)
        if "market_data: ok" not in doctor or "backend:" not in doctor:
            raise RuntimeError(
                f"Installed doctor did not validate the packaged configuration:\n{doctor}"
            )
        demo = run([*command, "demo"], directory=outside, environment=environment)
        if "SYNTHETIC" not in demo or any(
            policy not in demo for policy in ("twap", "vwap", "pov", "is_baseline")
        ):
            raise RuntimeError(f"Installed demo did not complete all four policies:\n{demo}")
        queries = run([*command, "db", "list"], directory=outside, environment=environment)
        if "01_execution_summary" not in queries or "07_data_quality_summary" not in queries:
            raise RuntimeError(f"Installed analytical queries are missing:\n{queries}")
        print("Installed wheel: doctor, synthetic demo and SQL listing passed outside the checkout")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path, help="Directory containing one built TradeForge wheel")
    args = parser.parse_args()
    wheels = sorted(args.dist.resolve().glob("tradeforge-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("Expected exactly one TradeForge wheel")
    check_wheel_resources(wheels[0], Path(__file__).resolve().parents[1])
    check_installed(wheels[0])


if __name__ == "__main__":
    main()
