"""Fail a C++ parity gate if the current build did not produce its extension."""

from __future__ import annotations

import importlib
import importlib.machinery
import sys
from pathlib import Path


def main(build_dir: Path) -> int:
    expected_dir = (build_dir / "python").resolve()
    try:
        core = importlib.import_module("tradeforge_core")
    except ImportError as exc:
        print(f"C++ extension missing from {expected_dir}: {exc}", file=sys.stderr)
        return 1

    module_path = Path(core.__file__).resolve()
    if module_path.parent != expected_dir or not any(
        module_path.name.endswith(suffix) for suffix in importlib.machinery.EXTENSION_SUFFIXES
    ):
        print(
            f"Expected a compiled tradeforge_core in {expected_dir}; imported {module_path}",
            file=sys.stderr,
        )
        return 1
    print(f"C++ parity target: {module_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else "build")))
