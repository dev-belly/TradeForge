"""Architecture tests.

These are the tests that keep the design from eroding. They walk the AST of
every module rather than grepping text, so a comment mentioning "import pandas"
does not trip them and a real import always does.

The rules enforced here are the ones the project's own design review states. A
rule that is not enforced by a test is a preference, not an invariant.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "tradeforge"

#: Layer -> the layers it may import from (transitively by convention).
ALLOWED_LAYER_IMPORTS: dict[str, frozenset[str]] = {
    "domain": frozenset({"domain"}),
    "orderbook": frozenset({"domain", "orderbook"}),
    "matching": frozenset({"domain", "matching"}),
    "microstructure": frozenset({"domain", "orderbook", "microstructure"}),
    "data": frozenset({"domain", "data", "orderbook"}),
    "queue": frozenset({"domain", "queue"}),
    "costs": frozenset({"domain", "costs"}),
    "replay": frozenset({"domain", "data", "microstructure", "execution", "replay", "orderbook"}),
    "execution": frozenset({"domain", "execution", "matching", "costs"}),
    "tca": frozenset({"domain", "execution", "tca", "microstructure"}),
    "ml": frozenset({"domain", "microstructure", "orderbook", "matching", "ml"}),
    "research": frozenset({"domain", "application", "infrastructure", "research", "tca"}),
    "application": frozenset(
        {
            "domain",
            "data",
            "orderbook",
            "queue",
            "costs",
            "replay",
            "execution",
            "microstructure",
            "tca",
            "infrastructure",
            "application",
            "matching",
        }
    ),
    "infrastructure": frozenset({"domain", "infrastructure"}),
    "storage": frozenset({"domain", "execution", "tca", "storage"}),
    "interfaces": frozenset(
        {
            "domain",
            "application",
            "data",
            "infrastructure",
            "ml",
            "research",
            "storage",
            "tca",
            "interfaces",
            "orderbook",
        }
    ),
}

FRAMEWORK_MODULES = frozenset(
    {"typer", "fastapi", "streamlit", "pydantic", "starlette", "uvicorn", "click"}
)

MAX_FILE_LINES = 600
MAX_FUNCTION_LINES = 100


@dataclass(frozen=True, slots=True)
class _ModuleAnalysis:
    """Everything the checks need, computed once per file."""

    imports: frozenset[str]
    layers: frozenset[str]
    file_lines: int
    long_functions: tuple[tuple[str, int], ...]
    has_star_import: bool


@cache
def _analyse(path: Path) -> _ModuleAnalysis:
    """Parse a file once. Six checks read the same AST; re-parsing per check
    made the suite cost O(checks x files) for no benefit."""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    imports: set[str] = set()
    star = False
    long_functions: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                star = True
            if node.level and node.module:
                imports.add(f"<relative>.{node.module}")
            elif node.module:
                imports.add(node.module)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", None)
            if end is not None and end - node.lineno + 1 > MAX_FUNCTION_LINES:
                long_functions.append((node.name, end - node.lineno + 1))

    layers: set[str] = set()
    for imported in imports:
        layers |= _resolved_layers(path, imported)

    return _ModuleAnalysis(
        imports=frozenset(imports),
        layers=frozenset(layers),
        file_lines=len(source.splitlines()),
        long_functions=tuple(long_functions),
        has_star_import=star,
    )


def _iter_modules() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _layer_of(path: Path) -> str:
    relative = path.relative_to(SRC)
    return relative.parts[0] if len(relative.parts) > 1 else "<root>"


def _imports_of(path: Path) -> set[str]:
    return set(_analyse(path).imports)


def _resolved_layers(path: Path, imported: str) -> set[str]:
    """Map an import name to the internal layers it touches."""
    if imported.startswith("<relative>"):
        module = imported.removeprefix("<relative>.")
        # Relative imports stay inside the package; resolve the layer by walking
        # up from the file's own directory.
        base = path.parent
        for _ in range(module.count(".")):
            base = base.parent
        candidate = base / module.split(".")[0]
        if candidate.is_dir():
            return {candidate.name}
        return {_layer_of(path)}
    if imported.startswith("tradeforge."):
        parts = imported.split(".")
        return {parts[1]} if len(parts) > 1 else set()
    return set()


# ------------------------------------------------------------------ layering


@pytest.mark.parametrize("path", _iter_modules(), ids=lambda p: str(p.relative_to(SRC)))
def test_domain_does_not_import_outward(path: Path) -> None:
    """The domain is the centre. It must not know anything about its callers."""
    if _layer_of(path) != "domain":
        pytest.skip("only applies to the domain layer")
    for imported in _imports_of(path):
        for layer in _resolved_layers(path, imported):
            assert layer == "domain", (
                f"{path.relative_to(SRC)} imports from the {layer!r} layer; "
                "the domain must not depend on anything outside itself"
            )


@pytest.mark.parametrize("path", _iter_modules(), ids=lambda p: str(p.relative_to(SRC)))
def test_layer_import_direction(path: Path) -> None:
    """Each layer may only import from layers it is allowed to know about."""
    layer = _layer_of(path)
    if layer not in ALLOWED_LAYER_IMPORTS:
        pytest.skip(f"layer {layer!r} is not part of the declared map")
    allowed = ALLOWED_LAYER_IMPORTS[layer]
    for imported in _imports_of(path):
        for target in _resolved_layers(path, imported):
            if target == layer:
                continue
            assert target in allowed, (
                f"{path.relative_to(SRC)} imports from {target!r}, which layer "
                f"{layer!r} is not allowed to depend on. Allowed: {sorted(allowed)}"
            )


@pytest.mark.parametrize("path", _iter_modules(), ids=lambda p: str(p.relative_to(SRC)))
def test_no_framework_in_core(path: Path) -> None:
    """Frameworks belong in the interface layer and nowhere else."""
    layer = _layer_of(path)
    if layer == "interfaces":
        pytest.skip("the interface layer is where frameworks are allowed")
    for imported in _imports_of(path):
        root = imported.split(".")[0].removeprefix("<relative>")
        assert root not in FRAMEWORK_MODULES, (
            f"{path.relative_to(SRC)} imports {imported!r}. Frameworks must stay "
            "in the interfaces layer; the core must be importable without them."
        )


def test_core_imports_without_optional_dependencies() -> None:
    """The domain, orderbook, matching and queue must not need any extra."""
    optional = {"numpy", "pandas", "pyarrow", "duckdb", "sklearn", "streamlit", "fastapi"}
    checked_layers = {"domain", "orderbook", "matching", "queue", "costs"}
    offenders: list[str] = []
    for path in _iter_modules():
        if _layer_of(path) not in checked_layers:
            continue
        for imported in _imports_of(path):
            root = imported.split(".")[0].removeprefix("<relative>")
            if root in optional:
                offenders.append(f"{path.relative_to(SRC)} -> {imported}")
    assert not offenders, (
        "the matching/book core must run on the standard library alone:\n  "
        + "\n  ".join(offenders)
    )


def test_no_time_sleep_in_simulation_path() -> None:
    """Latency is modelled by advancing a clock, never by sleeping.

    A `time.sleep` anywhere in the simulation path would make a latency
    experiment measure wall-clock time instead of market time, and would make
    the result depend on the machine it ran on.
    """
    offenders: list[str] = []
    for path in _iter_modules():
        if _layer_of(path) in {"interfaces", "data"}:
            continue  # the network adapter paces polls; that is not latency
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "sleep"
            ):
                offenders.append(f"{path.relative_to(SRC)}:{node.lineno}")
    assert not offenders, (
        "time.sleep found in the simulation path; model latency with the clock:\n  "
        + "\n  ".join(offenders)
    )


# -------------------------------------------------------------------- cycles


def test_no_import_cycles() -> None:
    """Detect cycles among internal modules by depth-first traversal."""
    graph: dict[str, set[str]] = {}
    for path in _iter_modules():
        module = ".".join(path.relative_to(SRC).with_suffix("").parts)
        if module.endswith(".__init__"):
            module = module.removesuffix(".__init__")
        edges: set[str] = set()
        for imported in _imports_of(path):
            if imported.startswith("tradeforge."):
                edges.add(imported.removeprefix("tradeforge."))
        graph[module] = edges

    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []
    cycles: list[str] = []

    def visit(node: str) -> None:
        if node in visited:
            return
        if node in visiting:
            cycles.append(" -> ".join([*stack, node]))
            return
        visiting.add(node)
        stack.append(node)
        for neighbour in sorted(graph.get(node, ())):
            # Only follow edges that resolve to a real module in this package.
            if neighbour in graph or any(key.startswith(neighbour + ".") for key in graph):
                visit(neighbour)
        stack.pop()
        visiting.discard(node)
        visited.add(node)

    for module in sorted(graph):
        visit(module)

    assert not cycles, "circular imports detected:\n  " + "\n  ".join(cycles)


# ------------------------------------------------------------------- size


@pytest.mark.parametrize("path", _iter_modules(), ids=lambda p: str(p.relative_to(SRC)))
def test_file_length(path: Path) -> None:
    lines = _analyse(path).file_lines
    assert lines <= MAX_FILE_LINES, (
        f"{path.relative_to(SRC)} has {lines} lines (limit {MAX_FILE_LINES}). "
        "A file this size is doing more than one job."
    )


@pytest.mark.parametrize("path", _iter_modules(), ids=lambda p: str(p.relative_to(SRC)))
def test_function_length(path: Path) -> None:
    """No function may sprawl. Long functions hide control flow."""
    offenders = [f"{name} ({length} lines)" for name, length in _analyse(path).long_functions]
    assert not offenders, f"{path.relative_to(SRC)} has over-long functions: {', '.join(offenders)}"


def test_no_module_named_engine_or_utils() -> None:
    """Named modules must describe a responsibility, not a catch-all."""
    banned = {"engine.py", "utils.py", "helpers.py", "common.py", "misc.py"}
    offenders = [
        str(path.relative_to(SRC))
        for path in _iter_modules()
        if path.name in banned and path.parent.name == "tradeforge"
    ]
    assert not offenders, f"catch-all module names found: {offenders}. Split by responsibility."


def test_package_has_no_star_imports() -> None:
    offenders = [
        str(path.relative_to(SRC)) for path in _iter_modules() if _analyse(path).has_star_import
    ]
    assert not offenders, f"star imports found in {offenders}"


def test_src_is_importable_path() -> None:
    assert str(SRC) in sys.path or str(ROOT / "src") in sys.path
