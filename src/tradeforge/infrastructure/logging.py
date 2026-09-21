"""Structured logging setup.

Logs are records of what the simulator did, not a debugging firehose. Two
formats: human (for `make demo`) and json (for CI / artifact pipelines).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

LOGGER_NAME = "tradeforge"


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Field names are stable for log queries."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "tradeforge", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class HumanFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s | %(message)s")


def configure_logging(*, level: str = "INFO", json_output: bool = False) -> logging.Logger:
    """Configure the `tradeforge` logger exactly once per process."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level.upper())
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if json_output else HumanFormatter())
    logger.addHandler(handler)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger(LOGGER_NAME if name is None else f"{LOGGER_NAME}.{name}")


def log_event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    """Attach structured fields to one record."""
    logger.log(level, message, extra={"tradeforge": fields})
