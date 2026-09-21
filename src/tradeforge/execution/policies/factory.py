"""Execution policy construction from configuration."""

from __future__ import annotations

from typing import Any

from ...domain.enums import PlacementStyle
from ...domain.exceptions import ConfigurationError
from ...domain.instrument import InstrumentSpec
from ...domain.orders import ParentOrder
from ...domain.protocols import ExecutionPolicy
from ..oms import Oms
from ..volume_profile import VolumeProfile
from .is_baseline import ImplementationShortfallPolicy
from .pov import PovPolicy
from .twap import TwapPolicy
from .vwap import VwapPolicy


def _style(value: str) -> PlacementStyle:
    try:
        return PlacementStyle(str(value).lower())
    except ValueError as exc:
        raise ConfigurationError(f"unknown placement style {value!r}") from exc


def build_policy(
    name: str,
    *,
    parent: ParentOrder,
    spec: InstrumentSpec,
    oms: Oms,
    execution_config: dict[str, Any],
    volume_profile: VolumeProfile | None = None,
    sigma_bps: float = 10.0,
) -> ExecutionPolicy:
    """Create an execution policy by name, wired to the shared OMS."""
    policies = execution_config.get("policies", {}) if isinstance(execution_config, dict) else {}
    placement = (
        execution_config.get("child_placement", {}) if isinstance(execution_config, dict) else {}
    )
    if not isinstance(policies, dict) or not isinstance(placement, dict):
        raise ConfigurationError("execution config must contain mappings")

    style = _style(str(placement.get("default_style", "passive")))
    key = name.strip().lower()

    if key == "twap":
        cfg = policies.get("twap", {}) if isinstance(policies.get("twap"), dict) else {}
        return TwapPolicy(
            parent=parent,
            spec=spec,
            oms=oms,
            style=style,
            n_slices=int(str(cfg.get("n_slices", 30))),
            end_of_window=str(cfg.get("end_of_window", "sweep_marketable")),
            round_to_lot=bool(cfg.get("round_to_lot", True)),
        )
    if key == "vwap":
        cfg = policies.get("vwap", {}) if isinstance(policies.get("vwap"), dict) else {}
        return VwapPolicy(
            parent=parent,
            spec=spec,
            oms=oms,
            style=style,
            n_slices=int(str(cfg.get("n_slices", 30))),
            end_of_window=str(cfg.get("end_of_window", "sweep_marketable")),
            profile=volume_profile,
        )
    if key == "pov":
        cfg = policies.get("pov", {}) if isinstance(policies.get("pov"), dict) else {}
        return PovPolicy(
            parent=parent,
            spec=spec,
            oms=oms,
            style=style,
            target_rate=float(str(cfg.get("target_rate", 0.05))),
            min_slice_base=int(str(cfg.get("min_slice_base", 0))),
            max_slice_base=int(str(cfg.get("max_slice_base", 1_000_000))),
            rebalance_interval_ns=int(str(cfg.get("rebalance_interval_ns", 60_000_000_000))),
            end_of_window=str(cfg.get("end_of_window", "sweep_marketable")),
        )
    if key == "is_baseline":
        cfg = (
            policies.get("is_baseline", {}) if isinstance(policies.get("is_baseline"), dict) else {}
        )
        return ImplementationShortfallPolicy(
            parent=parent,
            spec=spec,
            oms=oms,
            style=style,
            n_slices=int(str(cfg.get("n_slices", 30))),
            risk_aversion=float(str(cfg.get("risk_aversion", 1e-6))),
            # Explicit config wins; `sigma_bps` is the caller-supplied fallback.
            sigma_bps=float(str(cfg.get("sigma_bps", sigma_bps))),
            eta=float(str(cfg.get("eta", 1.0))),
            time_unit_ns=int(str(cfg.get("time_unit_ns", 1_000_000_000))),
            end_of_window=str(cfg.get("end_of_window", "sweep_marketable")),
        )
    raise ConfigurationError(f"unknown execution policy {name!r}")


AVAILABLE_POLICIES = ("twap", "vwap", "pov", "is_baseline")
