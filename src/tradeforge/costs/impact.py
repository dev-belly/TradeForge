"""Market impact model - SIMPLIFIED RESEARCH APPROXIMATION.

    square_root : impact_bps = k * sigma_bps * sqrt(participation_rate)
    linear      : impact_bps = k * sigma_bps * participation_rate

This is not a calibrated venue model. It is a monotone, transparent
approximation used to make the *cost of size* visible in TCA. Every report that
shows impact must carry the word "estimate".

Temporary vs permanent split:
    permanent_bps = impact_bps * permanent_fraction
    temporary_bps = impact_bps * (1 - permanent_fraction)
Permanent impact is the part that persists after the order completes; temporary
impact decays and is what gives rise to price reversion after execution.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImpactConfig:
    model: str = "square_root"
    k: float = 1.0
    permanent_fraction: float = 0.5
    min_bps: float = 0.0
    max_bps: float = 100.0

    @classmethod
    def from_dict(cls, payload: dict[str, object] | None) -> ImpactConfig:
        impact = payload.get("impact", {}) if isinstance(payload, dict) else {}
        if not isinstance(impact, dict):
            raise TypeError("impact config must contain an 'impact' mapping")
        return cls(
            model=str(impact.get("model", "square_root")),
            k=float(str(impact.get("k", 1.0))),
            permanent_fraction=float(str(impact.get("permanent_fraction", 0.5))),
            min_bps=float(str(impact.get("min_bps", 0.0))),
            max_bps=float(str(impact.get("max_bps", 100.0))),
        )


class SquareRootImpactModel:
    """Square-root impact in participation rate, scaled by volatility."""

    def __init__(self, config: ImpactConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "square_root_approximation"

    @property
    def config(self) -> ImpactConfig:
        return self._config

    def impact_bps(self, *, participation_rate: float, sigma_bps: float) -> float:
        rate = max(participation_rate, 0.0)
        raw = self._config.k * sigma_bps * (rate**0.5)
        return min(max(raw, self._config.min_bps), self._config.max_bps)

    def permanent_bps(self, *, participation_rate: float, sigma_bps: float) -> float:
        return self.impact_bps(participation_rate=participation_rate, sigma_bps=sigma_bps) * (
            self._config.permanent_fraction
        )

    def temporary_bps(self, *, participation_rate: float, sigma_bps: float) -> float:
        total = self.impact_bps(participation_rate=participation_rate, sigma_bps=sigma_bps)
        return total * (1.0 - self._config.permanent_fraction)

    def describe(self) -> dict[str, object]:
        return {
            "model": self.name,
            "k": self._config.k,
            "permanent_fraction": self._config.permanent_fraction,
            "caveat": "simplified research approximation, not calibrated",
        }


class LinearImpactModel:
    """Linear impact in participation rate."""

    def __init__(self, config: ImpactConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "linear_approximation"

    @property
    def config(self) -> ImpactConfig:
        return self._config

    def impact_bps(self, *, participation_rate: float, sigma_bps: float) -> float:
        rate = max(participation_rate, 0.0)
        raw = self._config.k * sigma_bps * rate
        return min(max(raw, self._config.min_bps), self._config.max_bps)

    def permanent_bps(self, *, participation_rate: float, sigma_bps: float) -> float:
        return self.impact_bps(participation_rate=participation_rate, sigma_bps=sigma_bps) * (
            self._config.permanent_fraction
        )

    def temporary_bps(self, *, participation_rate: float, sigma_bps: float) -> float:
        total = self.impact_bps(participation_rate=participation_rate, sigma_bps=sigma_bps)
        return total * (1.0 - self._config.permanent_fraction)

    def describe(self) -> dict[str, object]:
        return {
            "model": self.name,
            "k": self._config.k,
            "permanent_fraction": self._config.permanent_fraction,
            "caveat": "simplified research approximation, not calibrated",
        }


def build_impact_model(config: ImpactConfig) -> SquareRootImpactModel | LinearImpactModel:
    if config.model == "linear":
        return LinearImpactModel(config)
    if config.model == "none":
        none_config = ImpactConfig(
            model="none", k=0.0, permanent_fraction=config.permanent_fraction
        )
        return SquareRootImpactModel(none_config)
    return SquareRootImpactModel(config)
