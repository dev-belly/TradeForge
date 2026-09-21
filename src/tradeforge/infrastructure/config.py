"""YAML configuration loading.

Configs are data, not code: nothing in the domain or application layer reads a
file. Every parameter the core depends on (tick size, fees, latency, queue
policy) arrives through here, which is why there are no hard-coded market
constants anywhere else.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from ..domain.exceptions import ConfigurationError

DEFAULT_CONFIG_DIR = Path("configs")

REQUIRED_CONFIGS: tuple[str, ...] = (
    "market_data",
    "book",
    "replay",
    "queue",
    "latency",
    "execution",
    "costs",
    "impact",
    "research",
)


def load_yaml(path: Path | str) -> dict[str, Any]:
    """Read one YAML file into a plain mapping."""
    file_path = Path(path)
    if not file_path.exists():
        raise ConfigurationError(f"config file not found: {file_path}")
    try:
        with file_path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"invalid YAML in {file_path}: {exc}") from exc
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ConfigurationError(f"{file_path} must contain a mapping at the top level")
    return payload


def load_configs(directory: Path | str = DEFAULT_CONFIG_DIR) -> dict[str, dict[str, Any]]:
    """Load every `configs/*.yaml` file, keyed by stem."""
    root = Path(directory)
    if not root.is_dir():
        raise ConfigurationError(f"config directory not found: {root}")
    configs: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*.yaml")):
        configs[path.stem] = load_yaml(path)
    return configs


def validate_required(
    configs: Mapping[str, Any], required: tuple[str, ...] = REQUIRED_CONFIGS
) -> None:
    missing = [name for name in required if name not in configs]
    if missing:
        raise ConfigurationError(f"missing required configs: {', '.join(missing)}")


def section(configs: Mapping[str, Any], name: str) -> dict[str, Any]:
    """Return one config mapping, or an empty dict if it was not loaded."""
    payload = configs.get(name, {})
    if not isinstance(payload, dict):
        raise ConfigurationError(f"config {name!r} must be a mapping")
    return payload


def get_int(payload: Mapping[str, Any], key: str, default: int) -> int:
    value = payload.get(key, default)
    if value is None:
        return default
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{key!r} must be an integer, got {value!r}") from exc


def get_float(payload: Mapping[str, Any], key: str, default: float) -> float:
    value = payload.get(key, default)
    if value is None:
        return default
    try:
        return float(str(value))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{key!r} must be a number, got {value!r}") from exc


def get_str(payload: Mapping[str, Any], key: str, default: str) -> str:
    value = payload.get(key, default)
    return default if value is None else str(value)


def get_bool(payload: Mapping[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
