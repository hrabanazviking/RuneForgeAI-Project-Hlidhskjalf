"""In-process pub/sub + req/rep message bus for Project Hliðskjálf.

ZeroMQ-style semantics on the standard library: publishers emit
versioned envelopes, subscribers register per message type (with optional
``prefix.*`` wildcards), and request/reply pairs correlate over
``meta.correlation_id``. A single background dispatcher thread preserves
per-bus ordering; handler exceptions are logged, never propagated.

All traffic is envelope-wrapped (see :mod:`hlidskjalf.protocol.envelope`).
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from hlidskjalf.common import errors
from hlidskjalf.common.ids import new_id
from hlidskjalf.common.logging import get_logger
from hlidskjalf.protocol import envelope as env_mod

Handler = Callable[[Dict[str, Any]], Optional[Mapping[str, Any]]]

log = get_logger("protocol.bus")


class Bus:
    """Thread-safe in-process message bus."""

    def __init__(self, name: str = "bus", **options: Any) -> None:
        """Create the bus.

        Args:
            name: Bus name (used for the dispatcher thread and logs).
            options: Optional keyword options:
                ``maxsize`` — bound on the inbox queue (default 10000).
                ``config`` — a config object exposing ``get(dotted, default)``;
                    ``protocol.inbox_maxsize`` overrides ``maxsize`` when it
                    is a positive int.
        """
        unknown = set(options) - {"maxsize", "config"}
        if unknown:
            raise TypeError(f"unexpected Bus options: {sorted(unknown)}")
        self.name = name
        maxsize = options.get("maxsize", 10000)
        config = options.get("config")
        # Honour an explicit config value when provided; ignore anything that
        # is not a positive int so a bad config value cannot silently wedge
        # the inbox (fall back to the ``maxsize`` option instead).
        configured = (
            config.get("protocol.inbox_maxsize", None) if config is not None else None
        )
        if (
            isinstance(configured, int)
            and not isinstance(configured, bool)
            and configured > 0
        ):
            maxsize = configured
        self._inbox_maxsize = maxsize
        self._subs: Dict[str, List[Handler]] = {}
        self._wildcards: List[Tuple[str, Handler]] = []
        self._inbox: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=maxsize)
        self._pending: Dict[str, "queue.Queue[Dict[str, Any]]"] = {}
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._stats = {"published": 0, "delivered": 0, "handler_errors": 0,
                       "requests": 0, "replies": 0, "timeouts": 0,
                       "dropped": 0, "duplicate_replies_dropped": 0}
        self._started_at = time.time()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self) -> "Bus":
        """Start the background dispatcher thread (idempotent)."""
        with self._lock:
            if self._running:
                return self
            self._running = True
            self._thread = threading.Thread(
                target=self._dispatch_loop, name=f"bus-{self.name}", daemon=True
            )
            self._thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the dispatcher thread."""
        with self._lock:
            self._running = False
        if self._thread:
            self._inbox.put_nowait({"__bus_control__": "stop"})
            self._thread.join(timeout=timeout)
            self._thread = None

    def _dispatch_loop(self) -> None:
        while True:
            try:
                env = self._inbox.get(timeout=0.1)
            except queue.Empty:
                with self._lock:
                    if not self._running:
                        return
                continue
            if env.get("__bus_control__") == "stop":
                return
            self._deliver(env)

    # ------------------------------------------------------------------
    # subscription
    # ------------------------------------------------------------------
    def subscribe(self, msg_type: str, handler: Handler) -> Callable[[], None]:
        """Subscribe ``handler`` to a message type (or ``"prefix.*"``).

        Returns an ``unsubscribe()`` callable.
        """
        self.start()
        if msg_type.endswith("*"):
            with self._lock:
                self._wildcards.append((msg_type[:-1], handler))
        else:
            with self._lock:
                self._subs.setdefault(msg_type, []).append(handler)

        def unsubscribe() -> None:
            with self._lock:
                if msg_type.endswith("*"):
                    self._wildcards = [
                        (p, h) for p, h in self._wildcards if h is not handler
                    ]
                else:
                    handlers = self._subs.get(msg_type, [])
                    if handler in handlers:
                        handlers.remove(handler)

        return unsubscribe

    # ------------------------------------------------------------------
    # publishing
    # ------------------------------------------------------------------
    def publish(
        self,
        msg_type: str,
        payload: Mapping[str, Any],
        *,
        source: str = "",
        meta: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build an envelope and fan it out to subscribers."""
        env = env_mod.build(msg_type, payload, source=source, meta=meta)
        return self.publish_envelope(env)

    def publish_envelope(self, envelope: Mapping[str, Any]) -> Dict[str, Any]:
        """Fan out an already-built (and validated) envelope.

        Never blocks: on a full inbox the oldest queued message is shed
        (drop-oldest) and counted in ``stats()["dropped"]``.
        """
        env = env_mod.validate(envelope)
        self.start()
        with self._lock:
            self._stats["published"] += 1
        try:
            self._inbox.put_nowait(env)
        except queue.Full:
            # Bounded backpressure: shed the oldest queued message so the
            # producer never blocks and the dispatcher never starves.
            try:
                self._inbox.get_nowait()
            except queue.Empty:  # raced with the dispatcher; nothing to shed
                pass
            self._inbox.put_nowait(env)
            with self._lock:
                self._stats["dropped"] += 1
            log.warning(
                "bus inbox full: dropped oldest message",
                extra={"bus": self.name, "maxsize": self._inbox_maxsize},
            )
        return env

    # ------------------------------------------------------------------
    # request / reply
    # ------------------------------------------------------------------
    def request(
        self,
        msg_type: str,
        payload: Mapping[str, Any],
        *,
        timeout: float = 5.0,
        source: str = "",
        meta: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send a request and wait for the correlated reply.

        Raises:
            errors.TimeoutError: No reply arrived within ``timeout``.
        """
        corr = new_id()
        if timeout < 0:
            raise errors.ValidationError(
                f"request timeout must be >= 0, got {timeout!r}",
                details={"timeout": timeout, "msg_type": msg_type},
            )
        reply_q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=1)
        with self._lock:
            self._pending[corr] = reply_q
            self._stats["requests"] += 1
        merged_meta = dict(meta or {})
        merged_meta["correlation_id"] = corr
        try:
            self.publish(msg_type, payload, source=source, meta=merged_meta)
            reply = reply_q.get(timeout=timeout)
        except queue.Empty as exc:
            with self._lock:
                self._stats["timeouts"] += 1
            raise errors.TimeoutError(
                f"request {msg_type} timed out after {timeout}s",
                details={"msg_type": msg_type, "correlation_id": corr},
            ) from exc
        finally:
            with self._lock:
                self._pending.pop(corr, None)
        return reply

    def reply(
        self,
        request_envelope: Mapping[str, Any],
        payload: Mapping[str, Any],
        *,
        source: str = "",
    ) -> Dict[str, Any]:
        """Reply to a request envelope (correlates via ``correlation_id``)."""
        corr = env_mod.correlation_id(request_envelope)
        if not corr:
            raise errors.BusError(
                "cannot reply: request envelope has no correlation_id",
                details={"request_id": request_envelope.get("id")},
            )
        env = env_mod.build(
            f"{request_envelope.get('type')}.reply",
            payload,
            source=source,
            meta={"is_reply": True, "correlation_id": corr},
        )
        self.start()
        with self._lock:
            pending = self._pending.get(corr)
            self._stats["replies"] += 1
        if pending is not None:
            # Never block: the requester may already hold a reply in this
            # maxsize=1 queue (duplicate/late reply). Shed it and count it
            # rather than wedging the caller forever.
            try:
                pending.put_nowait(env)
            except queue.Full:
                with self._lock:
                    self._stats["duplicate_replies_dropped"] += 1
                log.warning(
                    "duplicate reply dropped (requester queue full)",
                    extra={"bus": self.name, "correlation_id": corr},
                )
        else:
            # Nobody is waiting; still fan out so observers can see it.
            self._inbox.put(env)
        return env

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _handlers_for(self, msg_type: str) -> List[Handler]:
        with self._lock:
            handlers = list(self._subs.get(msg_type, []))
            for prefix, handler in self._wildcards:
                if msg_type.startswith(prefix):
                    handlers.append(handler)
        return handlers

    def _deliver(self, env: Dict[str, Any]) -> None:
        corr = env_mod.correlation_id(env)
        if env_mod.is_reply(env) and corr:
            with self._lock:
                pending = self._pending.get(corr)
            if pending is not None:
                # Never block here: a duplicate/late reply for an already-
                # satisfied request must not wedge the dispatcher thread
                # (which would silently drop every later message). Shed the
                # duplicate, count it, and keep dispatching.
                try:
                    pending.put_nowait(env)
                except queue.Full:
                    with self._lock:
                        self._stats["duplicate_replies_dropped"] += 1
                    log.warning(
                        "duplicate reply dropped (queue full)",
                        extra={"bus": self.name, "correlation_id": corr},
                    )
                return
        for handler in self._handlers_for(str(env.get("type"))):
            try:
                handler(env)
            except Exception as exc:  # noqa: BLE001 - bus must not die
                with self._lock:
                    self._stats["handler_errors"] += 1
                log.warning(
                    "bus handler failed",
                    extra={"msg_type": env.get("type"), "error": str(exc)},
                )
        with self._lock:
            self._stats["delivered"] += 1

    # ------------------------------------------------------------------
    # introspection
    # ------------------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        """Return bus counters and subscription counts."""
        with self._lock:
            return {
                "name": self.name,
                "uptime_s": round(time.time() - self._started_at, 3),
                "running": self._running,
                "subscriptions": {
                    t: len(h) for t, h in self._subs.items()
                } | {"wildcards": len(self._wildcards)},
                "pending_requests": len(self._pending),
                **dict(self._stats),
            }
