"""Latency models.

Values here are SCENARIOS. TradeForge measures no hardware and has no exchange
timestamp deltas unless the source provides a receive timestamp, so `basis` is
recorded next to every result and reported as `scenario` unless observed data
exists.

Latency is applied by scheduling the order arrival on the simulation clock.
`time.sleep` is never used to model latency (enforced by a test).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from ..domain.enums import EventType
from ..domain.events import MarketEvent


@dataclass(frozen=True)
class LatencyComponents:
    decision_ns: int = 0
    network_ns: int = 0
    exchange_processing_ns: int = 0

    @property
    def total_ns(self) -> int:
        return self.decision_ns + self.network_ns + self.exchange_processing_ns


class ConstantLatencyModel:
    """Fixed scenario offset."""

    def __init__(self, components: LatencyComponents) -> None:
        self._components = components

    @property
    def basis(self) -> str:
        return "scenario"

    @property
    def components(self) -> LatencyComponents:
        return self._components

    def arrival_ns(self, decision_ns: int) -> int:
        return decision_ns + self._components.total_ns

    def describe(self) -> dict[str, object]:
        return {
            "model": "constant",
            "basis": self.basis,
            "decision_ns": self._components.decision_ns,
            "network_ns": self._components.network_ns,
            "exchange_processing_ns": self._components.exchange_processing_ns,
            "total_ns": self._components.total_ns,
        }


class LognormalLatencyModel:
    """Lognormal jitter around a median scenario. Seeded -> reproducible."""

    def __init__(self, components: LatencyComponents, sigma: float = 0.5, seed: int = 7) -> None:
        if sigma < 0:
            raise ValueError("sigma must be non-negative")
        self._components = components
        self._sigma = sigma
        self._rng = random.Random(seed)
        self._median = max(components.total_ns, 1)

    @property
    def basis(self) -> str:
        return "scenario"

    @property
    def components(self) -> LatencyComponents:
        return self._components

    def arrival_ns(self, decision_ns: int) -> int:
        draw = self._rng.lognormvariate(math.log(self._median), self._sigma)
        return decision_ns + round(draw)

    def describe(self) -> dict[str, object]:
        return {
            "model": "lognormal",
            "basis": self.basis,
            "median_ns": self._median,
            "sigma": self._sigma,
        }


class ObservedLatencyModel:
    """Uses receive - exchange timestamps when the dataset provides them.

    Falls back to the declared scenario only for events with no receive
    timestamp, and counts how often that happened so the report can say so.
    """

    def __init__(self, fallback: ConstantLatencyModel) -> None:
        self._fallback = fallback
        self._observed: list[int] = []
        self._n_missing = 0

    @property
    def basis(self) -> str:
        return "observed" if self._observed else self._fallback.basis

    def observe(self, event: MarketEvent) -> None:
        if event.receive_timestamp_ns is None:
            self._n_missing += 1
            return
        self._observed.append(max(event.receive_timestamp_ns - event.exchange_timestamp_ns, 0))

    def arrival_ns(self, decision_ns: int) -> int:
        if not self._observed:
            return self._fallback.arrival_ns(decision_ns)
        ordered = sorted(self._observed)
        return decision_ns + ordered[len(ordered) // 2]

    def describe(self) -> dict[str, object]:
        return {
            "model": "observed",
            "basis": self.basis,
            "n_observed": len(self._observed),
            "n_missing_receive_timestamp": self._n_missing,
        }


def build_latency_model(config: dict[str, object]) -> ConstantLatencyModel | LognormalLatencyModel:
    latency = config.get("latency", {}) if isinstance(config, dict) else {}
    if not isinstance(latency, dict):
        raise TypeError("latency config must be a mapping")
    parts = (
        latency.get("components_ns", {}) if isinstance(latency.get("components_ns"), dict) else {}
    )
    components = LatencyComponents(
        decision_ns=int(str(parts.get("decision", 0))),
        network_ns=int(str(parts.get("network", 0))),
        exchange_processing_ns=int(str(parts.get("exchange_processing", 0))),
    )
    model_name = str(latency.get("model", "constant")).lower()
    if model_name == "lognormal":
        lognormal = (
            latency.get("lognormal", {}) if isinstance(latency.get("lognormal"), dict) else {}
        )
        return LognormalLatencyModel(
            components,
            sigma=float(str(lognormal.get("sigma", 0.5))),
            seed=int(str(lognormal.get("seed", 7))),
        )
    return ConstantLatencyModel(components)


def is_book_event(event: MarketEvent) -> bool:
    return event.event_type is not EventType.HALT and event.event_type is not EventType.RESUME
