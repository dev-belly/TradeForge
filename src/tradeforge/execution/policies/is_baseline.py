"""Implementation-shortfall baseline (simplified Almgren-Chriss trajectory).

Continuous-time Almgren-Chriss optimal liquidation with linear temporary impact:

    x(t) = X * sinh(kappa * (T - t)) / sinh(kappa * T)
    kappa = sqrt(lambda * sigma^2 / eta)

where X is the initial holding, lambda the risk aversion, sigma the volatility
and eta the temporary-impact coefficient. Higher risk aversion front-loads the
schedule; lambda -> 0 recovers TWAP.

This is the standard textbook trajectory, NOT a calibrated model: eta is a
configuration constant and sigma is a past-window estimate supplied by the
caller. Documented as a simplified baseline.

Units (important - getting this wrong silently overflows `sinh`):

  * `sigma_bps` is a return volatility, so sigma = sigma_bps / 10_000;
  * the time axis is measured in `time_unit_ns` (default one second), because
    kappa has units of 1/time and must multiply a time in the same unit;
  * the dimensionless driver is `kappa * T`, reported by `describe()`.
    `kappa * T -> 0` recovers TWAP; larger values front-load the schedule.

`sinh(kappa * (T - t)) / sinh(kappa * T)` is evaluated through a numerically
stable branch: for large `kappa * T` the ratio tends to `exp(-kappa * t)`,
which avoids `math.range_error` when a risk-averse configuration is used.
"""

from __future__ import annotations

import math

from .base import Schedule, ScheduledPolicy


class ImplementationShortfallPolicy(ScheduledPolicy):
    def __init__(
        self,
        *,
        risk_aversion: float = 1e-6,
        sigma_bps: float = 10.0,
        eta: float = 1.0,
        time_unit_ns: int = 1_000_000_000,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        if risk_aversion < 0:
            raise ValueError("risk_aversion must be non-negative")
        if eta <= 0:
            raise ValueError("eta (temporary impact coefficient) must be positive")
        if time_unit_ns <= 0:
            raise ValueError("time_unit_ns must be positive")
        self._risk_aversion = risk_aversion
        self._sigma_bps = sigma_bps
        self._eta = eta
        self._time_unit_ns = time_unit_ns

    @property
    def name(self) -> str:
        return "is_baseline"

    @property
    def kappa(self) -> float:
        """sqrt(lambda * sigma^2 / eta), in units of 1 / time_unit_ns."""
        sigma = self._sigma_bps / 10_000.0
        return math.sqrt(self._risk_aversion * sigma * sigma / self._eta)

    @property
    def kappa_horizon(self) -> float:
        """The dimensionless driver `kappa * T` for this parent order."""
        duration_units = self._parent.duration_ns / self._time_unit_ns
        return self.kappa * duration_units

    def build_schedule(self) -> Schedule:
        n = self._n_slices
        kappa = self.kappa
        # Same time unit as kappa, or the sinh argument is off by 1e9.
        duration = self._parent.duration_ns / self._time_unit_ns
        # Remaining holding at the start of each slice, as a fraction of X.
        fractions: list[float] = []
        for i in range(n + 1):
            t = duration * i / n
            fractions.append(self._remaining_fraction(kappa, duration, t))
        weights = [fractions[i] - fractions[i + 1] for i in range(n)]
        total = sum(weights)
        if total <= 0:
            weights = [1.0 / n] * n
        else:
            weights = [w / total for w in weights]
        return Schedule(
            slice_times_ns=self._slice_times(),
            cumulative_targets=self._cumulative_from_weights(weights),
        )

    @staticmethod
    def _remaining_fraction(kappa: float, duration: float, t: float) -> float:
        """Stable evaluation of sinh(kappa*(T-t)) / sinh(kappa*T).

        Three regimes, all continuous:
          * kappa*T ~ 0  -> the linear limit (T - t) / T, i.e. TWAP;
          * moderate     -> the exact sinh ratio;
          * large        -> the exponential limit exp(-kappa*t), which is what
                            the ratio converges to once e^{+kappa*T} dominates.
        Without the third branch, `math.sinh` raises OverflowError.
        """
        argument = kappa * duration
        if argument < 1e-9:
            return (duration - t) / duration
        if argument > 50.0:
            return math.exp(-kappa * t)
        return math.sinh(kappa * (duration - t)) / math.sinh(argument)

    def describe(self) -> dict[str, object]:
        return {
            "algorithm": self.name,
            "risk_aversion": self._risk_aversion,
            "sigma_bps": self._sigma_bps,
            "eta": self._eta,
            "time_unit_ns": self._time_unit_ns,
            "kappa": self.kappa,
            "kappa_horizon": self.kappa_horizon,
            "caveat": "simplified Almgren-Chriss trajectory, not calibrated",
        }
