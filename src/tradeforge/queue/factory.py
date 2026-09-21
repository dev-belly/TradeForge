"""Queue model construction, gated by data capability."""

from __future__ import annotations

from ..domain.enums import CancelAheadPolicy, DataType, QueueMode
from ..domain.exceptions import ConfigurationError
from ..domain.protocols import QueueModel
from .approximate import ApproximateQueueModel
from .exact import ExactQueueModel


def create_queue_model(
    mode: QueueMode | str,
    data_type: DataType,
    policy: CancelAheadPolicy | str = CancelAheadPolicy.NEUTRAL,
) -> QueueModel:
    """Build the queue model the data can actually support.

    EXACT with L2 data is refused rather than silently degraded: an "exact"
    queue from aggregated levels would be a fabricated claim.
    """
    resolved_mode = mode if isinstance(mode, QueueMode) else QueueMode(str(mode).upper())
    resolved_policy = (
        policy if isinstance(policy, CancelAheadPolicy) else CancelAheadPolicy(str(policy).lower())
    )
    if resolved_mode is QueueMode.EXACT:
        return ExactQueueModel(data_type)
    if resolved_mode is QueueMode.APPROXIMATE:
        return ApproximateQueueModel(resolved_policy)
    raise ConfigurationError(f"unknown queue mode {resolved_mode!r}")


def queue_mode_from_config(
    config: dict[str, object], data_type: DataType
) -> tuple[QueueMode, CancelAheadPolicy]:
    queue = config.get("queue", {}) if isinstance(config, dict) else {}
    if not isinstance(queue, dict):
        raise ConfigurationError("queue config must be a mapping")
    mode = QueueMode(str(queue.get("mode", "approximate")).upper())
    policy = CancelAheadPolicy(str(queue.get("cancel_ahead_policy", "neutral")).lower())
    if mode is QueueMode.EXACT and data_type is not DataType.L3_MBO:
        raise ConfigurationError(
            f"queue mode exact requires L3_MBO data, source declares {data_type.value}"
        )
    return mode, policy
