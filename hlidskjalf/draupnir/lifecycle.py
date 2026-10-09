"""Draupnir lifecycle (Slice 24) — supervision of forged sub-agents.

The Supervisor watches every agent the forge creates: it kills agents that
overrun their timeout, restarts crashed agents up to a bounded limit
(default max 3 restarts), and collects results off the forge's result
queue. Polling is explicit — call :meth:`Supervisor.poll` (or ``spin``)
instead of spawning a background thread, keeping ownership obvious.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .forge import ConcurrencyLimitError, Forge, TaskSpec

MAX_RESTARTS = 3


@dataclass
class SupervisedAgent:
    """Book-keeping for one supervised task."""

    agent_id: str
    task_spec: TaskSpec
    status: str = "running"  # running | finished | failed | timeout | killed
    restarts: int = 0
    result: Optional[Dict[str, Any]] = None
    history: List[str] = field(default_factory=list)


class Supervisor:
    """Watches forged agents: timeouts, bounded restarts, result collection."""

    def __init__(
        self,
        forge: Forge,
        max_restarts: int = MAX_RESTARTS,
        poll_interval: float = 0.1,
    ) -> None:
        if max_restarts < 0:
            raise ValueError("max_restarts must be >= 0")
        self.forge = forge
        self.max_restarts = max_restarts
        self.poll_interval = poll_interval
        self._agents: Dict[str, SupervisedAgent] = {}
        self._specs: Dict[str, TaskSpec] = {}
        self._lock = threading.Lock()
        self._closed = False

    # -- submit -----------------------------------------------------------
    def submit(self, task_spec: TaskSpec) -> str:
        """Spawn a supervised agent for ``task_spec``; returns agent_id."""
        agent_id = self.forge.spawn(task_spec)
        with self._lock:
            self._agents[agent_id] = SupervisedAgent(
                agent_id=agent_id, task_spec=task_spec, history=[agent_id]
            )
            self._specs[agent_id] = task_spec
        return agent_id

    # -- polling ----------------------------------------------------------
    def poll(self) -> None:
        """One supervision round: collect results, enforce timeouts, restart."""
        self._sync_crashed()
        self._collect_results()
        self._enforce_timeouts()
        self._restart_crashed()

    def spin(self, duration: float) -> None:
        """Poll repeatedly for ``duration`` seconds."""
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline and not self._closed:
            self.poll()
            time.sleep(self.poll_interval)
        self.poll()

    def collect_results(self, timeout: float = 0.0) -> Dict[str, Dict[str, Any]]:
        """Drain the result queue; returns {agent_id: result}."""
        return self._collect_results(timeout=timeout)

    # -- queries ----------------------------------------------------------
    def list_agents(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {
                    "agent_id": a.agent_id,
                    "task_name": a.task_spec.name,
                    "status": a.status,
                    "restarts": a.restarts,
                    "history": list(a.history),
                    "result": a.result,
                }
                for a in self._agents.values()
            ]

    def get(self, agent_id: str) -> Optional[SupervisedAgent]:
        return self._agents.get(agent_id)

    def kill_agent(self, agent_id: str) -> bool:
        """Kill a running agent and mark it killed."""
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None or agent.status != "running":
                return False
            ok = self.forge.kill_agent(agent_id)
            agent.status = "killed"
            return ok

    def shutdown(self) -> None:
        self._closed = True
        self.forge.shutdown()

    # -- internals --------------------------------------------------------
    def _sync_crashed(self) -> None:
        """Mark agents whose process died without posting a result as failed."""
        statuses = {a["agent_id"]: a["status"] for a in self.forge.list_agents()}
        with self._lock:
            for agent_id, agent in self._agents.items():
                if agent.status == "running" and statuses.get(agent_id) == "crashed":
                    agent.status = "failed"
                    agent.result = {
                        "agent_id": agent_id,
                        "ok": False,
                        "output": None,
                        "error": "process crashed without posting a result",
                    }

    def _collect_results(self, timeout: float = 0.0) -> Dict[str, Dict[str, Any]]:
        collected: Dict[str, Dict[str, Any]] = {}
        end = time.monotonic() + timeout
        while True:
            try:
                item = self.forge.result_queue.get_nowait()
            except queue.Empty:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    item = self.forge.result_queue.get(
                        timeout=min(remaining, 0.05)
                    )
                except queue.Empty:
                    break
            agent_id = item.get("agent_id")
            with self._lock:
                agent = self._agents.get(agent_id)
            if agent is None:
                continue
            agent.result = item
            agent.status = "finished" if item.get("ok") else "failed"
            collected[agent_id] = item
        return collected

    def _enforce_timeouts(self) -> None:
        now = time.monotonic()
        with self._lock:
            snapshot = [
                (aid, a) for aid, a in self._agents.items() if a.status == "running"
            ]
        for agent_id, agent in snapshot:
            rec = self.forge._agents.get(agent_id)  # noqa: SLF001
            if rec is None:
                continue
            elapsed = now - rec.started_at
            if elapsed > agent.task_spec.timeout:
                if self.forge.kill_agent(agent_id):
                    with self._lock:
                        agent.status = "timeout"
                        agent.result = {
                            "agent_id": agent_id,
                            "ok": False,
                            "output": None,
                            "error": f"timeout after {agent.task_spec.timeout}s",
                        }

    def _restart_crashed(self) -> None:
        with self._lock:
            failed = [
                (aid, a)
                for aid, a in self._agents.items()
                if a.status == "failed" and a.restarts < self.max_restarts
            ]
        for agent_id, agent in failed:
            try:
                new_id = self.forge.spawn(agent.task_spec)
            except ConcurrencyLimitError:
                return  # try again on the next poll
            with self._lock:
                agent.restarts += 1
                agent.history.append(new_id)
                del self._agents[agent_id]
                self._agents[new_id] = agent
                agent.agent_id = new_id
                agent.status = "running"
                self._specs[new_id] = agent.task_spec
