"""Draupnir forge (Slice 23) — spawn sub-agents with heartbeat tracking.

Draupnir, Odin's ever-multiplying ring, forges new rings from itself; here
it forges sub-agent processes from task specs. Spawn uses a ``spawn``
multiprocessing context (safe under threads and on edge hardware). A shared
result queue carries agent outcomes; a manager-backed dict carries
heartbeats so the forge always knows who is alive.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

TaskFn = Callable[..., Any]


@dataclass
class TaskSpec:
    """What a forged sub-agent should do."""

    name: str
    fn: TaskFn
    args: Tuple[Any, ...] = ()
    kwargs: Dict[str, Any] = field(default_factory=dict)
    timeout: float = 60.0


class ConcurrencyLimitError(RuntimeError):
    """Raised when spawn would exceed the configured concurrency cap."""


def _agent_worker(
    agent_id: str,
    task_name: str,
    fn: TaskFn,
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
    result_queue: "mp.Queue",
    heartbeats: Dict[str, float],
    heartbeat_interval: float,
    dropped: "mp.Value",
) -> None:
    """Entry point of every forged process (runs in the child)."""

    def _pulse() -> None:
        while True:
            heartbeats[agent_id] = time.monotonic()
            time.sleep(heartbeat_interval)

    pulser = threading.Thread(target=_pulse, daemon=True, name=f"hb-{agent_id}")
    pulser.start()
    heartbeats[agent_id] = time.monotonic()

    try:
        payload = fn(*args, **kwargs)
        result = {"agent_id": agent_id, "ok": True, "output": payload}
    except Exception as exc:  # noqa: BLE001 — report crash back to parent
        result = {
            "agent_id": agent_id,
            "ok": False,
            "output": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    # Non-blocking put: a standalone Forge (no Supervisor draining the
    # queue) must never let a *finished* worker wedge forever on a full
    # queue — that reads as a healthy-but-stuck "running" agent and burns
    # a concurrency slot.  Drop the result (counted + logged) instead.
    try:
        result_queue.put_nowait(result)
    except queue.Full:
        with dropped.get_lock():
            dropped.value += 1
        log.warning(
            "forge: result queue full; dropping %s result for agent %s (%s)",
            "ok" if result.get("ok") else "error",
            agent_id,
            task_name,
        )
    finally:
        heartbeats.pop(agent_id, None)


@dataclass
class AgentRecord:
    """Parent-side view of one forged agent."""

    agent_id: str
    task_name: str
    process: "mp.Process"
    started_at: float
    status: str = "running"  # running | finished | killed | crashed
    restarts: int = 0
    last_result: Optional[Dict[str, Any]] = None


class Forge:
    """Spawns and tracks sub-agent processes with a concurrency cap."""

    def __init__(
        self,
        max_concurrency: int = 4,
        heartbeat_interval: float = 0.5,
        result_queue_size: int = 256,
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self.max_concurrency = max_concurrency
        self.heartbeat_interval = heartbeat_interval
        self._ctx = mp.get_context("spawn")
        self._manager = self._ctx.Manager()
        self._heartbeats: Dict[str, float] = self._manager.dict()
        self._result_queue: "mp.Queue" = self._ctx.Queue(result_queue_size)
        self._agents: Dict[str, AgentRecord] = {}
        self._lock = threading.Lock()
        #: Results dropped by workers when the result queue was full
        #: (shared with child processes; see :func:`_agent_worker`).
        self._dropped = self._ctx.Value("i", 0)

    # -- spawning ---------------------------------------------------------
    def spawn(self, task_spec: TaskSpec) -> str:
        """Forge a new sub-agent. Returns its agent_id.

        Raises ConcurrencyLimitError if ``max_concurrency`` running agents
        already exist.
        """
        with self._lock:
            self._reap_locked()
            running = sum(
                1 for r in self._agents.values() if r.status == "running"
            )
            if running >= self.max_concurrency:
                raise ConcurrencyLimitError(
                    f"max_concurrency ({self.max_concurrency}) reached"
                )
            agent_id = f"agent-{uuid.uuid4().hex[:8]}"
            proc = self._ctx.Process(
                target=_agent_worker,
                args=(
                    agent_id,
                    task_spec.name,
                    task_spec.fn,
                    task_spec.args,
                    task_spec.kwargs,
                    self._result_queue,
                    self._heartbeats,
                    self.heartbeat_interval,
                    self._dropped,
                ),
                name=agent_id,
                daemon=True,
            )
            record = AgentRecord(
                agent_id=agent_id,
                task_name=task_spec.name,
                process=proc,
                started_at=time.monotonic(),
            )
            self._agents[agent_id] = record
            proc.start()
            return agent_id

    # -- inspection -------------------------------------------------------
    def list_agents(self) -> List[Dict[str, Any]]:
        """Snapshot of every known agent (alive or reaped)."""
        with self._lock:
            self._reap_locked()
            return [self._record_view(r) for r in self._agents.values()]

    def get_heartbeat(self, agent_id: str) -> Optional[float]:
        """Seconds since the agent's last heartbeat (None if unknown)."""
        ts = self._heartbeats.get(agent_id)
        if ts is None:
            return None
        return time.monotonic() - ts

    def is_alive(self, agent_id: str) -> bool:
        rec = self._agents.get(agent_id)
        return bool(rec and rec.status == "running" and rec.process.is_alive())

    @property
    def result_queue(self) -> "mp.Queue":
        return self._result_queue

    @property
    def dropped_results(self) -> int:
        """Results workers dropped because the result queue was full."""
        return self._dropped.value

    def drain_results(self) -> List[Dict[str, Any]]:
        """Drain every available result from the result queue, non-blocking.

        Returns the drained result dicts in queue order.  A standalone
        Forge (no Supervisor) should call this periodically so finished
        workers' results never pile up.
        """
        drained: List[Dict[str, Any]] = []
        while True:
            try:
                drained.append(self._result_queue.get_nowait())
            except queue.Empty:
                break
        return drained

    # -- control ----------------------------------------------------------
    def kill_agent(self, agent_id: str) -> bool:
        """Terminate an agent. Returns True if it was running."""
        with self._lock:
            rec = self._agents.get(agent_id)
            if rec is None or rec.status != "running":
                return False
            rec.process.terminate()
            rec.process.join(timeout=2.0)
            if rec.process.is_alive():
                rec.process.kill()
                rec.process.join(timeout=1.0)
            rec.status = "killed"
            self._heartbeats.pop(agent_id, None)
            return True

    def shutdown(self) -> None:
        """Terminate every running agent and release manager resources."""
        with self._lock:
            for rec in self._agents.values():
                if rec.status == "running":
                    rec.process.terminate()
            for rec in self._agents.values():
                if rec.status == "running":
                    rec.process.join(timeout=2.0)
                    rec.status = "killed"
        try:
            self._manager.shutdown()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass

    # -- internals --------------------------------------------------------
    def _reap_locked(self) -> None:
        for rec in self._agents.values():
            if rec.status == "running" and not rec.process.is_alive():
                rec.status = "finished" if rec.process.exitcode == 0 else "crashed"
                self._heartbeats.pop(rec.agent_id, None)

    @staticmethod
    def _record_view(rec: AgentRecord) -> Dict[str, Any]:
        return {
            "agent_id": rec.agent_id,
            "task_name": rec.task_name,
            "pid": rec.process.pid,
            "status": rec.status,
            "restarts": rec.restarts,
            "started_at": rec.started_at,
        }
