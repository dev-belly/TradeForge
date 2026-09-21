"""Application layer: build the whole stack from configuration and run it.

This module is the only place that knows how the pieces fit together. The domain
does not import from here, and nothing here contains market logic: it reads
config, constructs objects, and executes one replay.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..costs.fees import FeeConfig, FeeModel
from ..costs.impact import ImpactConfig, build_impact_model
from ..data.registry import create_adapter
from ..data.validation import EventValidator, ValidationReport, ValidationSettings
from ..domain.enums import DataType, PlacementStyle, Side
from ..domain.exceptions import ConfigurationError
from ..domain.instrument import InstrumentSpec
from ..domain.orders import ParentOrder
from ..domain.protocols import MarketDataAdapter
from ..execution.guards import GuardConfig, Guardrails
from ..execution.oms import Oms
from ..execution.policies.factory import build_policy
from ..execution.result import ExecutionResult
from ..execution.simulator import ExecutionSimulator, SimulatorSettings
from ..execution.volume_profile import (
    VolumeProfile,
    compute_volume_profile,
    merge_profiles,
)
from ..infrastructure.config import get_int, get_str, section
from ..infrastructure.logging import get_logger, log_event
from ..microstructure.features import MicrostructureEngine
from ..orderbook import BookSettings, create_book
from ..queue.factory import create_queue_model, queue_mode_from_config
from ..replay import (
    ReplayOutcome,
    ReplayRunner,
    ReplaySettings,
    SimulationClock,
    build_latency_model,
)
from ..tca import (
    DEFAULT_HORIZONS_NS,
    MarketObserver,
    TcaReport,
    build_tca_report,
    max_markout_horizon_ns,
)


@dataclass(frozen=True)
class RunRequest:
    """One execution run: policy plus the knobs an experiment varies."""

    policy: str = "twap"
    style: str | None = None
    quantity_base: int | None = None
    latency_ns: int | None = None
    queue_policy: str | None = None
    n_slices: int | None = None
    #: POV target participation. Realised participation is an output.
    pov_target_rate: float | None = None
    seed: int | None = None
    max_events: int | None = None
    run_id: str = ""

    def label(self) -> str:
        parts = [self.policy]
        if self.style:
            parts.append(self.style)
        if self.latency_ns is not None:
            parts.append(f"lat{self.latency_ns}")
        if self.queue_policy:
            parts.append(self.queue_policy)
        if self.quantity_base is not None:
            parts.append(f"q{self.quantity_base}")
        return "-".join(parts)


@dataclass
class _Wiring:
    """Every component one run needs, assembled but not yet connected.

    Exists so `run` reads as a sequence of steps rather than a 150-line
    constructor. Nothing here is shared between runs: a second run gets a fresh
    book, OMS, queue model and clock.
    """

    spec: InstrumentSpec
    data_type: DataType
    book_cfg: dict[str, Any]
    replay_cfg: dict[str, Any]
    execution_cfg: dict[str, Any]
    parent: ParentOrder
    book: Any
    validator: EventValidator
    features: MicrostructureEngine
    clock: SimulationClock
    queue_model: Any
    latency: Any
    fees: FeeModel
    impact: Any
    guards: Guardrails
    oms: Oms
    policy: Any
    profile: VolumeProfile | None
    outcome: ReplayOutcome
    horizons: tuple[int, ...]
    observer: MarketObserver
    adapter: MarketDataAdapter


@dataclass
class RunContext:
    """Everything one run built, kept for inspection and debugging."""

    request: RunRequest
    result: ExecutionResult | None = None
    outcome: ReplayOutcome = field(default_factory=ReplayOutcome)
    validation: ValidationReport | None = None
    tca: TcaReport | None = None
    spec: InstrumentSpec | None = None
    parent: ParentOrder | None = None
    provenance: dict[str, Any] = field(default_factory=dict)


def config_fingerprint(configs: Mapping[str, Any]) -> str:
    """Stable hash of the effective config, recorded with every result."""
    payload = json.dumps(configs, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


class ExecutionHarness:
    """Builds and runs executions from a configuration mapping."""

    def __init__(self, configs: Mapping[str, Any]) -> None:
        self._configs: dict[str, Any] = dict(configs)
        self._logger = get_logger("harness")
        self.fingerprint = config_fingerprint(self._configs)

    # ------------------------------------------------------------------ build

    @classmethod
    def from_directory(cls, directory: Any) -> ExecutionHarness:
        from ..infrastructure.config import load_configs, validate_required

        configs = load_configs(directory)
        validate_required(configs)
        return cls(configs)

    @property
    def configs(self) -> dict[str, Any]:
        return self._configs

    def spec(self) -> InstrumentSpec:
        market = section(self._configs, "market_data")
        return InstrumentSpec.from_dict(section(market, "instrument"))

    def data_type(self) -> DataType:
        dataset = section(section(self._configs, "market_data"), "dataset")
        return DataType(get_str(dataset, "data_type", "L2_MBP"))

    def adapter(self, *, seed: int | None = None) -> MarketDataAdapter:
        source = section(section(self._configs, "market_data"), "source")
        options = dict(section(source, "options"))
        if seed is not None:
            options["seed"] = seed
        return create_adapter(get_str(source, "adapter", "synthetic"), options)

    def parent_order(self, request: RunRequest) -> ParentOrder:
        spec = self.spec()
        market = section(self._configs, "market_data")
        source_options = section(section(market, "source"), "options")
        execution = section(self._configs, "execution")
        parent_cfg = section(execution, "parent_order")
        session_start = get_int(source_options, "start_time_ns", 0)
        start_ns = session_start + get_int(parent_cfg, "start_offset_ns", 0)
        end_ns = start_ns + get_int(parent_cfg, "end_offset_ns", 0)
        quantity = request.quantity_base or get_int(parent_cfg, "quantity_base", 10_000)
        return ParentOrder(
            parent_order_id=request.run_id or f"{request.label()}@{start_ns}",
            symbol=spec.symbol,
            side=Side(get_str(parent_cfg, "side", "BUY").upper()),
            quantity_base=quantity,
            start_ns=start_ns,
            end_ns=end_ns,
            urgency=float(str(parent_cfg.get("urgency", 0.5))),
            participation_limit=float(str(parent_cfg.get("participation_limit", 0.05))),
        )

    def historical_profile(self, parent: ParentOrder, n_buckets: int) -> VolumeProfile:
        """VWAP needs a profile from PRIOR sessions - never the current one."""
        market = section(self._configs, "market_data")
        source = section(market, "source")
        options = dict(section(source, "options"))
        execution = section(self._configs, "execution")
        vwap_cfg = section(section(execution, "policies"), "vwap")
        n_sessions = get_int(vwap_cfg, "historical_lookback_sessions", 3)
        base_seed = get_int(options, "seed", 20260908)
        profiles: list[VolumeProfile] = []
        for offset in range(1, n_sessions + 1):
            prior = self.adapter(seed=base_seed - offset)
            profiles.append(
                compute_volume_profile(
                    prior.events(),
                    n_buckets=n_buckets,
                    start_ns=parent.start_ns,
                    end_ns=parent.end_ns,
                    source=f"{source.get('adapter', 'synthetic')}-prior-session",
                )
            )
        return merge_profiles(profiles, source="synthetic-prior-sessions")

    # -------------------------------------------------------------------- run

    def run(self, request: RunRequest) -> RunContext:
        """Build the stack, replay once, and return the execution plus its TCA."""
        context = RunContext(request=request, spec=self.spec())
        wiring = self._assemble(request, context)
        simulator = self._simulator(wiring, request)
        runner = self._runner(wiring, request)

        log_event(
            self._logger,
            20,
            "starting execution run",
            policy=request.policy,
            style=request.style,
            quantity_base=wiring.parent.quantity_base,
            data_type=wiring.data_type.value,
            queue_mode=wiring.queue_model.mode.value,
            latency_basis=wiring.latency.basis,
        )
        result = runner.run(simulator)

        context.result = result
        context.validation = wiring.validator.report
        context.provenance = self.provenance(context, wiring)
        context.tca = build_tca_report(
            result,
            wiring.observer,
            wiring.spec,
            provenance={
                **context.provenance,
                "policy": request.policy,
                "queue_mode": result.queue_mode,
                "latency_basis": result.latency_basis,
                "counterfactual_mode": result.counterfactual_mode,
            },
            markout_horizons_ns=wiring.horizons,
        )
        return context

    def _assemble(self, request: RunRequest, context: RunContext) -> _Wiring:
        """Construct every component one run needs. Nothing is shared between runs."""
        spec = self.spec()
        data_type = self.data_type()
        book_cfg = section(self._configs, "book")
        replay_cfg = section(self._configs, "replay")
        queue_cfg = section(self._configs, "queue")
        execution_cfg = self._execution_config(request)

        parent = self.parent_order(request)
        context.parent = parent
        outcome = ReplayOutcome()
        context.outcome = outcome

        book = create_book(
            symbol=spec.symbol,
            settings=BookSettings.from_dict(book_cfg),
            data_type=data_type,
            spec=spec,
            on_violation=ReplayRunner.violation_collector(outcome),  # type: ignore[arg-type]
        )
        validator = EventValidator(
            ValidationSettings.from_dict({"mode": get_str(replay_cfg, "validation_mode", "STRICT")})
        )
        mode, cancel_policy = queue_mode_from_config(queue_cfg, data_type)
        if request.queue_policy:
            cancel_policy = type(cancel_policy)(str(request.queue_policy).lower())

        oms = Oms()
        policy, profile = self._policy(request, execution_cfg, parent, spec, oms)
        horizons = self.markout_horizons()

        return _Wiring(
            spec=spec,
            data_type=data_type,
            book_cfg=book_cfg,
            replay_cfg=replay_cfg,
            execution_cfg=execution_cfg,
            parent=parent,
            book=book,
            validator=validator,
            features=MicrostructureEngine(),
            clock=SimulationClock(parent.start_ns),
            queue_model=create_queue_model(mode, data_type, cancel_policy),
            latency=build_latency_model(self._latency_config(request)),
            fees=FeeModel(FeeConfig.from_dict(section(self._configs, "costs"))),
            impact=build_impact_model(ImpactConfig.from_dict(section(self._configs, "impact"))),
            guards=Guardrails(GuardConfig.from_dict(replay_cfg)),
            oms=oms,
            policy=policy,
            profile=profile,
            outcome=outcome,
            horizons=horizons,
            # Markouts need data after the window, so the observer and the replay
            # both extend past the parent end by the longest horizon. Without
            # this every markout would be reported as unmeasurable.
            observer=MarketObserver(
                start_ns=parent.start_ns,
                end_ns=parent.end_ns + max_markout_horizon_ns(horizons),
            ),
            adapter=self.adapter(seed=request.seed),
        )

    def _policy(
        self,
        request: RunRequest,
        execution_cfg: dict[str, Any],
        parent: ParentOrder,
        spec: InstrumentSpec,
        oms: Oms,
    ) -> tuple[Any, VolumeProfile | None]:
        """Build the policy, supplying a PRIOR-session profile for VWAP only."""
        n_slices = request.n_slices or get_int(
            section(section(execution_cfg, "policies"), request.policy), "n_slices", 30
        )
        profile = (
            self.historical_profile(parent, max(n_slices, 1))
            if request.policy.strip().lower() == "vwap"
            else None
        )
        policy = build_policy(
            request.policy,
            parent=parent,
            spec=spec,
            oms=oms,
            execution_config=execution_cfg,
            volume_profile=profile,
        )
        return policy, profile

    def _simulator(self, wiring: _Wiring, request: RunRequest) -> ExecutionSimulator:
        placement = section(wiring.execution_cfg, "child_placement")
        passive = section(placement, "passive")
        return ExecutionSimulator(
            parent=wiring.parent,
            spec=wiring.spec,
            oms=wiring.oms,
            policy=wiring.policy,
            book=wiring.book,  # type: ignore[arg-type]
            queue_model=wiring.queue_model,
            latency=wiring.latency,
            guards=wiring.guards,
            fees=wiring.fees,
            clock=wiring.clock,
            settings=SimulatorSettings(
                snapshot_depth=get_int(section(wiring.book_cfg, "book"), "snapshot_depth", 10),
                passive_offset_ticks=get_int(passive, "price_offset_ticks", 0),
                max_levels_crossed=self._max_levels_crossed(wiring.execution_cfg),
                passive_timeout_ns=get_int(passive, "timeout_ns", 0),
                counterfactual_mode=get_str(
                    section(wiring.replay_cfg, "replay"),
                    "counterfactual_mode",
                    "replay_approximation",
                ),
            ),
        )

    def _runner(self, wiring: _Wiring, request: RunRequest) -> ReplayRunner:
        stop_ns = wiring.parent.end_ns + max_markout_horizon_ns(wiring.horizons)
        return ReplayRunner(
            events=wiring.adapter.events(),
            book=wiring.book,
            features=wiring.features,
            clock=wiring.clock,
            validator=wiring.validator,
            settings=ReplaySettings(
                snapshot_depth=get_int(section(wiring.book_cfg, "book"), "snapshot_depth", 10),
                max_events=request.max_events,
                stop_after_ns=stop_ns,
            ),
            outcome=wiring.outcome,
            observers=(wiring.observer,),
        )

    def markout_horizons(self) -> tuple[int, ...]:
        tca_cfg = section(section(self._configs, "costs"), "tca")
        raw = tca_cfg.get("markout_horizons_ns")
        if not isinstance(raw, (list, tuple)) or not raw:
            return DEFAULT_HORIZONS_NS
        horizons = sorted({int(str(value)) for value in raw})
        if horizons[0] <= 0:
            raise ConfigurationError("markout horizons must be positive nanoseconds")
        return tuple(horizons)

    # --------------------------------------------------------------- helpers

    def _execution_config(self, request: RunRequest) -> dict[str, Any]:
        """Copy of the execution config with run-level overrides applied."""
        base: dict[str, Any] = copy.deepcopy(dict(section(self._configs, "execution")))
        if request.style:
            placement = base.setdefault("child_placement", {})
            try:
                PlacementStyle(str(request.style).lower())
            except ValueError as exc:
                raise ConfigurationError(f"unknown placement style {request.style!r}") from exc
            placement["default_style"] = request.style
        if request.n_slices:
            policies = base.setdefault("policies", {})
            policy_cfg = policies.setdefault(request.policy.strip().lower(), {})
            if isinstance(policy_cfg, dict):
                policy_cfg["n_slices"] = request.n_slices
        if request.pov_target_rate is not None:
            rate = float(request.pov_target_rate)
            if not 0.0 < rate <= 1.0:
                raise ConfigurationError(f"pov_target_rate must be in (0, 1], got {rate}")
            pov_cfg = base.setdefault("policies", {}).setdefault("pov", {})
            if isinstance(pov_cfg, dict):
                pov_cfg["target_rate"] = rate
        return base

    @staticmethod
    def _max_levels_crossed(execution_cfg: dict[str, Any]) -> int | None:
        """`null` in YAML means "no sweep depth limit"."""
        placement = section(execution_cfg, "child_placement")
        aggressive = section(placement, "aggressive")
        value = aggressive.get("max_levels_crossed")
        if value is None:
            return None
        try:
            parsed = int(str(value))
        except (TypeError, ValueError) as exc:
            raise ConfigurationError(
                f"max_levels_crossed must be an integer or null, got {value!r}"
            ) from exc
        return parsed if parsed > 0 else None

    def _latency_config(self, request: RunRequest) -> dict[str, Any]:
        base: dict[str, Any] = copy.deepcopy(dict(section(self._configs, "latency")))
        if request.latency_ns is not None:
            latency = base.setdefault("latency", {})
            components = latency.setdefault("components_ns", {})
            if isinstance(components, dict):
                components["decision"] = 0
                components["network"] = int(request.latency_ns)
                components["exchange_processing"] = 0
        return base

    def provenance(self, context: RunContext, wiring: _Wiring) -> dict[str, Any]:
        """Everything needed to reproduce and to interpret a result."""
        market = section(self._configs, "market_data")
        dataset = section(market, "dataset")
        profile = wiring.profile
        return {
            "config_fingerprint": self.fingerprint,
            "dataset_name": get_str(dataset, "name", "unknown"),
            "dataset_version": get_str(dataset, "version", "unknown"),
            "provenance": get_str(dataset, "provenance", "unknown"),
            "data_type": wiring.data_type.value,
            "instrument": {
                "symbol": wiring.spec.symbol,
                "tick_size": str(wiring.spec.tick_size),
                "lot_size": wiring.spec.lot_size,
            },
            "volume_profile_source": profile.source if profile else None,
            "impact_model": str(wiring.impact.name),
            "impact_model_description": wiring.impact.describe(),
            "impact_caveat": ("simplified research approximation, not calibrated to any venue"),
            "replay": {
                "counterfactual_mode": get_str(
                    section(section(self._configs, "replay"), "replay"),
                    "counterfactual_mode",
                    "replay_approximation",
                ),
                "events_consumed": context.outcome.n_events,
            },
        }
