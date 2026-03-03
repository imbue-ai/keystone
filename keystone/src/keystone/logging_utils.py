"""Logging utilities for Keystone."""

import logging
from datetime import UTC, datetime


class ISOFormatter(logging.Formatter):
    """Log formatter with ISO 8601 timestamps including milliseconds and timezone."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: ARG002
        dt = datetime.fromtimestamp(record.created, tz=UTC).astimezone()
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(record.msecs):03d}{dt.strftime('%z')}"
