"""Locate editable working-directory resources or installed defaults."""

from __future__ import annotations

from pathlib import Path

PACKAGE_RESOURCE_ROOT = Path(__file__).resolve().parents[1] / "_resources"


def default_resource_dir(name: str) -> Path:
    """Prefer an existing local directory when defaults are requested.

    Explicit paths are handled by their loaders and never use this fallback.
    Installed wheels also carry defaults, so the CLI needs no source checkout.
    """
    local = Path(name)
    return local if local.is_dir() else PACKAGE_RESOURCE_ROOT / name
