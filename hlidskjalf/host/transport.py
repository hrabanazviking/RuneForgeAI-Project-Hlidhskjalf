"""Slice 43 — Bidirectional transport abstraction (Phase G).

Host↔edge link between the workstation and the edge co-processor. The
interface is intentionally small:

    connect()          — establish (or re-establish) the link
    send(msg)          — enqueue/transmit one message (a JSON-serializable dict)
    recv(timeout)      — wait for one inbound message; raises TimeoutError
    close()            — release resources; idempotent

Implementations:
    InMemoryTransport — a loopback pair for tests and dev: ``create_pair()``
                        returns (a, b) such that a.send/recv <-> b.send/recv.
    HttpTransport     — STUB. Carries the full reconnect/backoff logic
                        structure (attempt counting, exponential backoff with
                        jitter, state machine) but performs no real network I/O:
                        there is no server to talk to in this slice. Documented
                        as the extension point for a real HTTP/SSE or WebSocket
                        implementation in a later slice.
"""

from __future__ import annotations

import queue
import random
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Optional


class TransportError(Exception):
    """Raised when the transport cannot fulfil an operation."""


class Transport(ABC):
    """Bidirectional message transport interface."""

    @abstractmethod
    def connect(self) -> None:
        """Establish (or re-establish) the link. Idempotent."""

    @abstractmethod
    def send(self, msg: dict[str, Any]) -> None:
        """Transmit one message. Raises TransportError if not connected."""

    @abstractmethod
    def recv(self, timeout: Optional[float] = None) -> dict[str, Any]:
        """Return the next inbound message, waiting up to ``timeout`` seconds.

        ``timeout=None`` blocks indefinitely. Raises ``TimeoutError`` on
        expiry and ``TransportError`` if the transport is closed.
        """

    @abstractmethod
    def close(self) -> None:
        """Release resources. Idempotent."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """True while the link is usable."""


class InMemoryTransport(Transport):
    """In-process loopback transport for tests and development.

    Messages are delivered as deep references through thread-safe queues;
    a pair created with :meth:`create_pair` is fully bidirectional —
    everything ``a`` sends is received by ``b`` and vice versa.
    """

    def __init__(
        self,
        inbound: "queue.Queue[dict[str, Any]]",
        outbound: "queue.Queue[dict[str, Any]]",
        name: str = "mem",
    ) -> None:
        self._inbound = inbound
        self._outbound = outbound
        self._name = name
        self._closed = False
        self._lock = threading.Lock()

    @classmethod
    def create_pair(
        cls, name_a: str = "a", name_b: str = "b"
    ) -> tuple["InMemoryTransport", "InMemoryTransport"]:
        """Return ``(a, b)`` with a.send/recv <-> b.send/recv wiring."""
        a_to_b: "queue.Queue[dict[str, Any]]" = queue.Queue()
        b_to_a: "queue.Queue[dict[str, Any]]" = queue.Queue()
        return (
            cls(inbound=b_to_a, outbound=a_to_b, name=name_a),
            cls(inbound=a_to_b, outbound=b_to_a, name=name_b),
        )

    def connect(self) -> None:
        with self._lock:
            self._closed = False

    def send(self, msg: dict[str, Any]) -> None:
        with self._lock:
            if self._closed:
                raise TransportError(f"{self._name}: transport is closed")
            self._outbound.put(dict(msg))

    def recv(self, timeout: Optional[float] = None) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                raise TransportError(f"{self._name}: transport is closed")
        try:
            return self._inbound.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError(f"{self._name}: recv timed out") from exc

    def close(self) -> None:
        with self._lock:
            self._closed = True

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return not self._closed


class HttpTransport(Transport):
    """STUB — HTTP host↔edge transport with reconnect logic structure.

    This slice defines the *shape* of the production transport: connection
    state machine, reconnect attempts with exponential backoff and jitter,
    and failure accounting. It performs **no real network I/O** — there is no
    server endpoint in this slice, so :meth:`connect` raises
    :class:`TransportError` after exhausting its configured attempts. A later
    slice replaces ``_dial`` with a real HTTP/SSE (or WebSocket) session while
    reusing the backoff and state machinery unchanged.

    Reconnect policy (config, not code — pass at construction):
        max_attempts  — connection attempts before giving up (0 = infinite)
        base_delay    — first backoff delay in seconds
        max_delay     — backoff ceiling in seconds
        jitter        — random fraction added to each delay
    """

    # Connection states.
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    BACKOFF = "backoff"

    def __init__(
        self,
        url: str,
        *,
        max_attempts: int = 5,
        base_delay: float = 0.5,
        max_delay: float = 30.0,
        jitter: float = 0.1,
    ) -> None:
        self.url = url
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter
        self.state = self.DISCONNECTED
        self.attempts = 0
        self.consecutive_failures = 0
        self.last_error: Optional[str] = None
        self._closed = True
        self._outbox: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._inbox: "queue.Queue[dict[str, Any]]" = queue.Queue()

    # -- internals -----------------------------------------------------
    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with jitter for the given 1-based attempt."""
        delay = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        return delay + random.uniform(0, self.jitter * delay)

    def _dial(self) -> None:
        """Open the underlying connection. STUB: always fails.

        A later slice implements this (e.g. ``urllib`` POST + SSE stream, or a
        WebSocket). It must raise on failure and return once the session is
        usable; the reconnect machinery above handles everything else.
        """
        raise TransportError(
            f"HttpTransport stub: no real server to dial at {self.url} "
            "(wire _dial() in a later slice)"
        )

    # -- Transport interface -------------------------------------------
    def connect(self) -> None:
        """Attempt to connect, retrying per the backoff policy.

        Raises :class:`TransportError` when attempts are exhausted.
        Idempotent once connected.
        """
        if self.state == self.CONNECTED:
            return
        self._closed = False
        self.state = self.CONNECTING
        attempt = 0
        while self.max_attempts == 0 or attempt < self.max_attempts:
            attempt += 1
            self.attempts += 1
            try:
                self._dial()
            except TransportError as exc:
                self.last_error = str(exc)
                self.consecutive_failures += 1
                self.state = self.BACKOFF
                delay = self._backoff_delay(attempt)
                time.sleep(delay)
                continue
            self.state = self.CONNECTED
            self.consecutive_failures = 0
            self.last_error = None
            return
        self.state = self.DISCONNECTED
        raise TransportError(
            f"could not connect to {self.url} after {attempt} attempt(s): "
            f"{self.last_error}"
        )

    def send(self, msg: dict[str, Any]) -> None:
        if self._closed or self.state != self.CONNECTED:
            raise TransportError("HttpTransport is not connected")
        # Stub: queue for the future real sender; keeps the interface honest.
        self._outbox.put(dict(msg))

    def recv(self, timeout: Optional[float] = None) -> dict[str, Any]:
        if self._closed:
            raise TransportError("HttpTransport is closed")
        try:
            return self._inbox.get(timeout=timeout)
        except queue.Empty as exc:
            raise TimeoutError("HttpTransport: recv timed out") from exc

    def close(self) -> None:
        self._closed = True
        self.state = self.DISCONNECTED

    @property
    def is_connected(self) -> bool:
        return not self._closed and self.state == self.CONNECTED

    def reconnect(self) -> None:
        """Drop the current session and run the connect cycle again."""
        self.close()
        self.connect()
