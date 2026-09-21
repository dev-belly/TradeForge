"""Bridge to the compiled C++ core.

The compiled extension is **optional**. TradeForge must run, and its test suite
must pass, with nothing but a Python toolchain: a reviewer who cannot build C++
should still be able to reproduce every number. What the bridge refuses to do is
pretend. The active backend is reported in every benchmark artefact, and a
benchmark recorded against the Python reference is labelled as such.

ADR-002 defines the contract:

  * the C++ core owns event decode, the book, matching and replay;
  * the Python implementation is the *reference oracle*, kept because it is the
    specification the C++ must agree with, and because differential testing
    needs two independent implementations;
  * `tradeforge.backend` says which one produced a given result.
"""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass
from types import ModuleType
from typing import Any

CORE_MODULE_NAME = "tradeforge_core"
PYTHON_BACKEND = "python-reference"
CPP_BACKEND = "cpp-core"


@dataclass(frozen=True, slots=True)
class CoreStatus:
    """Whether the compiled core is importable, and what that means."""

    available: bool
    backend: str
    detail: str
    version: str | None = None
    module_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "backend": self.backend,
            "detail": self.detail,
            "version": self.version,
            "module_path": self.module_path,
        }


def find_core_spec() -> Any:
    """Locate the extension without importing it (importing has side effects)."""
    try:
        return importlib.util.find_spec(CORE_MODULE_NAME)
    except (ImportError, ValueError):
        return None


def core_status() -> dict[str, object]:
    """Report the active backend. Never claims C++ when it is not there."""
    spec = find_core_spec()
    if spec is None:
        return CoreStatus(
            available=False,
            backend=PYTHON_BACKEND,
            detail=(
                f"compiled core '{CORE_MODULE_NAME}' is not importable; running the "
                "Python reference implementation. Build it with `make build-cpp`."
            ),
        ).to_dict()
    try:
        module = importlib.import_module(CORE_MODULE_NAME)
    except ImportError as exc:
        return CoreStatus(
            available=False,
            backend=PYTHON_BACKEND,
            detail=f"compiled core found but failed to import: {exc}",
        ).to_dict()
    return CoreStatus(
        available=True,
        backend=CPP_BACKEND,
        detail="compiled core is importable and will be used for book, matching and replay.",
        version=str(getattr(module, "__version__", "unknown")),
        module_path=str(getattr(module, "__file__", None)),
    ).to_dict()


def load_core(*, required: bool = False) -> ModuleType | None:
    """Import the compiled core, or return None.

    `required=True` raises instead of returning None, for callers (the
    differential test suite, the benchmark runner) where a silent fallback would
    invalidate the result.
    """
    try:
        return importlib.import_module(CORE_MODULE_NAME)
    except ImportError as exc:
        if required:
            raise ImportError(
                f"{CORE_MODULE_NAME} is required here but not importable. "
                "Build it with `make build-cpp` before running this code path."
            ) from exc
        return None


def backend_name() -> str:
    return str(core_status()["backend"])


def has_cpp_core() -> bool:
    return bool(core_status()["available"])
