"""Adapter base class.

Source-specific decoding lives in adapters and nowhere else. The core must never
contain `if source == ...`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from ...domain.enums import DataType
from ...domain.events import MarketEvent
from ...domain.exceptions import AdapterError


class BaseAdapter(ABC):
    """Common plumbing: option access with typed defaults."""

    def __init__(self, options: dict[str, object] | None = None) -> None:
        self._options: dict[str, object] = dict(options or {})

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def data_type(self) -> DataType:
        """Declared capability. Never optimistic: this gates every feature."""

    @abstractmethod
    def events(self) -> Iterator[MarketEvent]: ...

    # ------------------------------------------------------------- helpers

    def opt_str(self, key: str, default: str) -> str:
        value = self._options.get(key, default)
        return str(value)

    def opt_int(self, key: str, default: int) -> int:
        value = self._options.get(key, default)
        try:
            return int(str(value))
        except (TypeError, ValueError) as exc:
            raise AdapterError(f"{self.name}: option {key} is not an int: {value!r}") from exc

    def opt_float(self, key: str, default: float) -> float:
        value = self._options.get(key, default)
        try:
            return float(str(value))
        except (TypeError, ValueError) as exc:
            raise AdapterError(f"{self.name}: option {key} is not a float: {value!r}") from exc

    def opt_bool(self, key: str, default: bool) -> bool:
        value = self._options.get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
