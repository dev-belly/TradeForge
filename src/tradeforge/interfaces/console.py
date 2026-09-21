"""Console formatting helpers.

Kept separate from the CLI wiring so that the same tables can be produced by the
API and by report generation. Nothing here computes a number; it only decides
how a number is presented, and it never presents one without its basis.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..domain.exceptions import ConfigurationError


def render_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    aligns: Sequence[str] | None = None,
    max_width: int = 160,
) -> str:
    """Fixed-width table. Right-aligns numerics when `aligns` is omitted."""
    materialised = [[_cell(v) for v in row] for row in rows]
    if not materialised:
        return "(no rows)"

    resolved = list(aligns) if aligns else [_default_align(h, materialised) for h in headers]
    if len(resolved) != len(headers):
        raise ConfigurationError("aligns must match the number of headers")

    widths = [
        max(len(str(headers[i])), *(len(row[i]) for row in materialised))
        for i in range(len(headers))
    ]
    lines = [
        "  ".join(_pad(str(headers[i]), widths[i], resolved[i]) for i in range(len(headers))),
        "  ".join("-" * widths[i] for i in range(len(headers))),
    ]
    for row in materialised:
        lines.append("  ".join(_pad(row[i], widths[i], resolved[i]) for i in range(len(headers))))
    text = "\n".join(lines)
    if max_width and max(len(line) for line in lines) > max_width:
        return text  # do not truncate numbers; let the terminal wrap
    return text


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        if value != value:  # NaN
            return "n/a"
        if value == int(value) and abs(value) < 1e15:
            return f"{value:.0f}"
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _default_align(header: str, rows: Sequence[Sequence[str]]) -> str:
    for row in rows:
        if row[0] and _is_numeric(row[0]):
            return "right"
    return "left"


def _is_numeric(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return text not in {"-", "n/a"}


def _pad(text: str, width: int, align: str) -> str:
    return text.rjust(width) if align == "right" else text.ljust(width)


def render_mapping(payload: Mapping[str, Any], *, indent: int = 0) -> str:
    """Two-column key/value rendering, nested one level for readability."""
    prefix = " " * indent
    lines: list[str] = []
    for key, value in payload.items():
        if isinstance(value, Mapping):
            lines.append(f"{prefix}{key}:")
            lines.append(render_mapping(value, indent=indent + 2))
        elif isinstance(value, (list, tuple)):
            lines.append(f"{prefix}{key}: {', '.join(_cell(v) for v in value) or '(none)'}")
        else:
            lines.append(f"{prefix}{key}: {_cell(value)}")
    return "\n".join(lines)


def caveat_block(caveats: Sequence[str], *, title: str = "Caveats") -> str:
    if not caveats:
        return ""
    lines = [f"{title}:"]
    lines.extend(f"  - {c}" for c in caveats)
    return "\n".join(lines)


def provenance_block(provenance: Mapping[str, Any]) -> str:
    """Always printed with a result. A number without provenance is a rumour."""
    keep = (
        "dataset_name",
        "provenance",
        "data_type",
        "queue_mode",
        "latency_basis",
        "counterfactual_mode",
        "config_fingerprint",
        "impact_model",
        "volume_profile_source",
    )
    subset = {k: provenance[k] for k in keep if k in provenance}
    return render_mapping(subset or provenance)
