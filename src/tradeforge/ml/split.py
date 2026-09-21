"""Time-ordered, purged, embargoed train/validation/test split.

Three rules, each of which exists because the alternative silently inflates
measured performance:

  1. **No shuffling.** `random.train_test_split` on time-series data lets the
     model train on the future and test on the past. The split is by index
     order, and `assert_time_ordered` verifies the input really is sorted.
  2. **Purging.** A sample's label looks `horizon_ns` into the future. Samples
     near a boundary therefore peek into the next split, so the last
     `embargo_samples` of each block are dropped.
  3. **No re-use.** Each sample belongs to exactly one split; the constructor
     verifies the index sets are disjoint.

The purge count is derived from the label horizon and the sampling interval,
not guessed: `ceil(horizon_ns / sample_interval_ns)`, floored at the configured
`embargo_events` so a short horizon cannot silently disable the embargo.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PurgedSplit:
    """Index sets for each split, after purging."""

    train: tuple[int, ...]
    validation: tuple[int, ...]
    test: tuple[int, ...]
    n_samples: int
    n_purged: int
    embargo_samples: int
    train_frac: float
    validation_frac: float
    test_frac: float

    @property
    def sizes(self) -> dict[str, int]:
        return {
            "train": len(self.train),
            "validation": len(self.validation),
            "test": len(self.test),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "n_samples": self.n_samples,
            "sizes": self.sizes,
            "n_purged": self.n_purged,
            "embargo_samples": self.embargo_samples,
            "train_frac": self.train_frac,
            "validation_frac": self.validation_frac,
            "test_frac": self.test_frac,
            "shuffled": False,
            "purged": True,
        }


def required_embargo_samples(
    *, horizon_ns: int, sample_interval_ns: int, configured_embargo_ns: int = 0
) -> int:
    """How many samples to drop at each boundary.

    A label issued at time `t` is only resolved at `t + horizon_ns`, so every
    sample within one horizon of a boundary overlaps the next split.

    The embargo is expressed in **nanoseconds**, like every other duration in
    this project. Expressing it in "events" is a trap: the number of samples per
    event depends on the sampling interval, so a value that looks harmless
    ("100 events") can silently wipe out an entire validation block when the
    sampler runs every five seconds.
    """
    if sample_interval_ns <= 0:
        raise ValueError("sample_interval_ns must be positive")
    horizon_span = math.ceil(max(horizon_ns, 0) / sample_interval_ns)
    configured_span = math.ceil(max(configured_embargo_ns, 0) / sample_interval_ns)
    return max(horizon_span, configured_span, 0)


MIN_SPLIT_FRACTION = 0.05


def assert_splits_usable(split: PurgedSplit, *, min_fraction: float = MIN_SPLIT_FRACTION) -> None:
    """Fail loudly when an embargo has eaten a split.

    Without this check an over-large embargo produces a validation set of six
    samples, every metric is computed on noise, and nothing says so.
    """
    floor = max(int(split.n_samples * min_fraction), 1)
    for name, size in split.sizes.items():
        if size < floor:
            raise ValueError(
                f"split {name!r} has only {size} of {split.n_samples} samples "
                f"(minimum {floor}). The embargo of {split.embargo_samples} samples "
                "is too large for this dataset - widen the sampling window, shorten "
                "the label horizon, or reduce embargo_ns."
            )


def purged_time_split(
    n_samples: int,
    *,
    train_frac: float = 0.6,
    validation_frac: float = 0.2,
    test_frac: float = 0.2,
    embargo_samples: int = 0,
) -> PurgedSplit:
    """Contiguous, time-ordered split with a purge at each boundary."""
    if n_samples <= 0:
        raise ValueError("cannot split an empty sample")
    total = train_frac + validation_frac + test_frac
    if abs(total - 1.0) > 1e-9:
        raise ValueError(f"split fractions must sum to 1, got {total}")
    for name, value in (
        ("train_frac", train_frac),
        ("validation_frac", validation_frac),
        ("test_frac", test_frac),
    ):
        if value < 0:
            raise ValueError(f"{name} must be non-negative")

    first = int(n_samples * train_frac)
    second = int(n_samples * (train_frac + validation_frac))
    embargo = max(int(embargo_samples), 0)

    train = tuple(range(0, max(first - embargo, 0)))
    validation = tuple(range(first, max(second - embargo, first)))
    test = tuple(range(second, n_samples))
    # Count what was actually dropped from each block. Measuring the second
    # block as `second - len(validation)` would assume it starts at zero and
    # overstate the purge by the whole width of the training block.
    purged_from_train = first - len(train)
    purged_from_validation = (second - first) - len(validation)
    n_purged = purged_from_train + purged_from_validation

    return PurgedSplit(
        train=train,
        validation=validation,
        test=test,
        n_samples=n_samples,
        n_purged=n_purged,
        embargo_samples=embargo,
        train_frac=train_frac,
        validation_frac=validation_frac,
        test_frac=test_frac,
    )


def assert_disjoint(split: PurgedSplit) -> None:
    """Each sample belongs to at most one split. Raises on overlap."""
    seen: set[int] = set()
    for name, indices in (
        ("train", split.train),
        ("validation", split.validation),
        ("test", split.test),
    ):
        overlap = seen & set(indices)
        if overlap:
            raise ValueError(f"split {name} overlaps earlier splits at {sorted(overlap)[:5]}")
        seen |= set(indices)


def assert_time_ordered(timestamps_ns: Sequence[int]) -> None:
    """Raises if timestamps are not non-decreasing.

    Called before splitting, because a split of unsorted data is not a
    time-ordered split no matter how it is labelled.
    """
    for previous, current in itertools.pairwise(timestamps_ns):
        if current < previous:
            raise ValueError(
                f"timestamps are not ordered: {current} follows {previous}. "
                "Sorting them would silently reorder the future."
            )
