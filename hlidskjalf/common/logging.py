"""Structured JSON logging for Project Hliðskjálf.

Standard-library based (``logging``). Emits one JSON object per line so the
edge node can ship logs to any collector without extra dependencies.

Contract (consumed by other phases, e.g. kista/_compat.py):
    get_logger(name) -> logging.Logger
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, TextIO

#: Log record attributes that are NOT copied into the JSON ``context`` block.
_RESERVED = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "taskName",
    }
)


class JsonFormatter(logging.Formatter):
    """Format a log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        context = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED
        }
        if context:
            entry["context"] = context
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, separators=(",", ":"))


def setup_logging(
    level: str = "INFO",
    *,
    json: bool = True,
    stream: Optional[TextIO] = None,
    force: bool = False,
) -> logging.Logger:
    """Configure the ``hlidskjalf`` root logger.

    Args:
        level: Root log level name (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json: Emit single-line JSON; when False, use a human-readable format.
        stream: Output stream; defaults to stderr.
        force: Replace existing handlers instead of leaving them in place.

    Returns:
        The configured ``hlidskjalf`` logger.
    """
    root = logging.getLogger("hlidskjalf")
    root.setLevel(level.upper())
    if root.handlers and not force:
        return root
    if force:
        root.handlers.clear()
    handler = logging.StreamHandler(stream or sys.stderr)
    if json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
    root.addHandler(handler)
    root.propagate = False
    return root


def get_logger(name: str) -> logging.Logger:
    """Return a stdlib logger namespaced under ``hlidskjalf``.

    Child loggers inherit the JSON handler installed by :func:`setup_logging`.
    """
    qualified = name if name.startswith("hlidskjalf") else f"hlidskjalf.{name}"
    return logging.getLogger(qualified)


class _ContextAdapter(logging.LoggerAdapter):
    """LoggerAdapter that folds keyword args into the JSON context block."""

    def log(self, level: int, msg: object, *args: Any, **kwargs: Any) -> None:
        passthrough = {
            key: kwargs.pop(key)
            for key in ("exc_info", "stack_info", "stacklevel")
            if key in kwargs
        }
        extra: dict[str, Any] = dict(self.extra or {})
        extra.update(kwargs.pop("extra", None) or {})
        extra.update(kwargs)  # remaining kwargs are context fields
        self.logger.log(level, msg, *args, extra=extra, **passthrough)


def bind(logger: logging.Logger, **context: Any) -> _ContextAdapter:
    """Return an adapter that injects ``context`` into every record.

    Extra keyword arguments on any log call are folded into the JSON
    ``context`` block as well:

    Example:
        log = bind(get_logger("heimdall"), request_id="abc123")
        log.info("authenticated", principal="muse")
    """
    return _ContextAdapter(logger, context)


def log_kv(
    logger: logging.Logger,
    level: str,
    message: str,
    context: Optional[Mapping[str, Any]] = None,
    **kwargs: Any,
) -> None:
    """Log ``message`` at ``level`` with structured ``context`` fields."""
    merged: dict[str, Any] = dict(context or {})
    merged.update(kwargs)
    logger.log(getattr(logging, level.upper()), message, extra=merged)
