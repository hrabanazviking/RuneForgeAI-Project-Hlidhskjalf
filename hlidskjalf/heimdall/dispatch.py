"""Heimdall message dispatcher.

Routes validated envelopes to the handler registered for their message
type. Unknown types land in the dead-letter queue (bounded, in-memory)
instead of vanishing silently. Handler failures are captured as
:class:`~hlidskjalf.common.errors.DispatchError` and also dead-lettered.

Handlers receive the full validated envelope and return a JSON-serializable
payload (dict); the gateway wraps it into a response envelope.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Mapping, Optional

from hlidskjalf.common.errors import DispatchError, NotFoundError
from hlidskjalf.common.logging import bind, get_logger

log = get_logger("heimdall.dispatch")

#: Handler signature: envelope -> result payload (a JSON object).
Handler = Callable[[Dict[str, Any]], Mapping[str, Any]]


class Dispatcher:
    """Routes envelopes by message type with a dead-letter queue."""

    def __init__(self, dead_letter_max: int = 1000) -> None:
        self._handlers: Dict[str, Handler] = {}
        self._dead_letter: Deque[Dict[str, Any]] = deque(maxlen=dead_letter_max)
        self._lock = threading.RLock()  # re-entrant: health() calls routes()
        self._started_at = time.time()
        self._stats = {"dispatched": 0, "errors": 0, "dead_lettered": 0}

    # ------------------------------------------------------------------
    # routing table
    # ------------------------------------------------------------------
    def register(self, msg_type: str, handler: Handler) -> None:
        """Register (or replace) the handler for ``msg_type``."""
        with self._lock:
            self._handlers[msg_type] = handler

    def unregister(self, msg_type: str) -> bool:
        """Remove a handler; return True when one existed."""
        with self._lock:
            return self._handlers.pop(msg_type, None) is not None

    def routes(self) -> List[str]:
        """Registered message types."""
        with self._lock:
            return sorted(self._handlers)

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------
    def dispatch(self, envelope: Mapping[str, Any]) -> Dict[str, Any]:
        """Route one validated envelope; return the handler's result payload.

        Raises:
            NotFoundError: No handler for the message type (dead-lettered).
            DispatchError: The handler raised (dead-lettered).
        """
        env = dict(envelope)
        msg_type = str(env.get("type", ""))
        audit = bind(log, msg_type=msg_type, msg_id=env.get("id"))
        with self._lock:
            handler = self._handlers.get(msg_type)
        if handler is None:
            self._to_dead_letter(env, f"no handler for message type {msg_type!r}")
            audit.warning("dead-lettered: unknown type")
            raise NotFoundError(
                f"no handler for message type {msg_type!r}",
                details={"msg_type": msg_type},
            )
        try:
            result = handler(env)
        except Exception as exc:  # noqa: BLE001 - captured, not propagated raw
            self._to_dead_letter(env, f"handler raised: {exc}")
            with self._lock:
                self._stats["errors"] += 1
            audit.warning("handler failed", error=str(exc))
            raise DispatchError(
                f"handler for {msg_type!r} failed: {exc}",
                details={"msg_type": msg_type, "cause": str(exc)},
            ) from exc
        if not isinstance(result, Mapping):
            raise DispatchError(
                f"handler for {msg_type!r} must return a mapping",
                details={"msg_type": msg_type},
            )
        with self._lock:
            self._stats["dispatched"] += 1
        audit.debug("dispatched")
        return dict(result)

    # ------------------------------------------------------------------
    # dead-letter queue
    # ------------------------------------------------------------------
    def _to_dead_letter(self, envelope: Mapping[str, Any], reason: str) -> None:
        with self._lock:
            self._dead_letter.append(
                {
                    "ts": time.time(),
                    "reason": reason,
                    "type": envelope.get("type"),
                    "id": envelope.get("id"),
                    "envelope": dict(envelope),
                }
            )
            self._stats["dead_lettered"] += 1

    def dead_letter(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Most recent dead-lettered envelopes (newest first)."""
        with self._lock:
            items = list(self._dead_letter)
        return list(reversed(items))[:limit]

    def purge_dead_letter(self) -> int:
        """Empty the dead-letter queue; return the number dropped."""
        with self._lock:
            count = len(self._dead_letter)
            self._dead_letter.clear()
        return count

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------
    def health(self) -> Dict[str, Any]:
        """Health snapshot for the gateway health report."""
        with self._lock:
            return {
                "status": "ok",
                "uptime_s": round(time.time() - self._started_at, 3),
                "handlers": self.routes(),
                "dead_letter_count": len(self._dead_letter),
                **dict(self._stats),
            }
