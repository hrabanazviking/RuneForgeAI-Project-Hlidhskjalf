"""Neural workload scheduler for the Hailo NPU pipeline.

Skald: the NPU is a single scarce resource shared by embeddings, TTS,
STT, and whatever the cognitive stack dreams up.  :class:`NeuralScheduler`
queues work as jobs, runs the highest-priority job first, and keeps FIFO
order within a priority level.  To guarantee no starvation, every
scheduling round ages the jobs that were *not* chosen: a job's
effective priority rises the longer it waits, so even the humblest job
eventually outranks everything newer.

Priorities are integers; larger numbers run first.  Ties break by
submission order (FIFO).  Job functions run synchronously in
:meth:`NeuralScheduler.run_next` — the scheduler is a policy layer, not
a thread pool.

Only the standard library is used.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

#: Job lifecycle states.
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"

#: How much effective priority a skipped job gains per scheduling round.
AGING_STEP = 1


@dataclass
class Job:
    """One unit of neural work."""

    id: str
    kind: str
    status: str = QUEUED
    priority: int = 0
    #: Monotonic submission order; FIFO tie-break within a priority.
    seq: int = 0
    #: Effective priority after aging; scheduler-internal.
    effective_priority: int = 0
    result: Any = None
    error: Optional[BaseException] = None
    _fn: Optional[Callable[[], Any]] = field(
        default=None, repr=False, compare=False
    )

    def run(self) -> Any:
        """Execute the job's function, recording status and result."""
        if self._fn is None:
            raise RuntimeError(f"job {self.id} has no function to run")
        self.status = RUNNING
        try:
            self.result = self._fn()
        except BaseException as exc:  # noqa: BLE001 — recorded, not swallowed
            self.error = exc
            self.status = FAILED
            raise
        self.status = DONE
        return self.result


class NeuralScheduler:
    """Priority queue with aging for NPU jobs.

    ``submit`` returns a job id; :meth:`run_next` executes the highest
    effective-priority queued job and returns it.  Jobs are kept until
    read via :meth:`get`, so results can be inspected after completion.
    """

    def __init__(self, aging_step: int = AGING_STEP) -> None:
        self._aging_step = aging_step
        self._jobs: Dict[str, Job] = {}
        self._queue: List[Job] = []
        self._seq = itertools.count()
        self._ids = itertools.count(1)

    def submit(
        self,
        fn: Callable[[], Any],
        kind: str,
        priority: int = 0,
    ) -> str:
        """Queue ``fn`` as a job of ``kind`` with the given priority.

        Returns:
            The job id (``"job-N"``).

        Raises:
            TypeError: If ``fn`` is not callable.
        """
        if not callable(fn):
            raise TypeError(f"fn must be callable, got {type(fn).__name__}")
        job_id = f"job-{next(self._ids)}"
        seq = next(self._seq)
        job = Job(
            id=job_id,
            kind=kind,
            priority=priority,
            seq=seq,
            effective_priority=priority,
            _fn=fn,
        )
        self._jobs[job_id] = job
        self._queue.append(job)
        return job_id

    def pending(self) -> List[Job]:
        """Queued jobs in the order they would run (best first)."""
        return sorted(
            (j for j in self._queue if j.status == QUEUED),
            key=lambda j: (-j.effective_priority, j.seq),
        )

    def get(self, job_id: str) -> Job:
        """Return the job with ``job_id`` (any status)."""
        try:
            return self._jobs[job_id]
        except KeyError:
            raise KeyError(f"unknown job id {job_id!r}") from None

    def __len__(self) -> int:
        return len(self._queue)

    def _pop_next(self) -> Optional[Job]:
        """Remove and return the best queued job, aging the skipped ones."""
        candidates = self.pending()
        if not candidates:
            return None
        chosen = candidates[0]
        for job in candidates[1:]:
            job.effective_priority += self._aging_step
        self._queue.remove(chosen)
        return chosen

    def run_next(self) -> Optional[Job]:
        """Run the highest-priority queued job and return it.

        Returns ``None`` when the queue is empty.  Jobs that are
        skipped gain ``aging_step`` effective priority so no job
        starves forever.  If the job's function raises, the job is
        marked failed, the exception propagates, and the scheduler
        stays usable.
        """
        job = self._pop_next()
        if job is None:
            return None
        job.run()
        return job

    def run_all(self) -> List[Job]:
        """Run every queued job in scheduling order; return them all.

        A failing job records its error and does not stop the rest.
        """
        finished: List[Job] = []
        while True:
            job = self._pop_next()
            if job is None:
                break
            try:
                job.run()
            except BaseException:  # noqa: BLE001 — recorded on the job
                pass
            finished.append(job)
        return finished

    def clear(self) -> None:
        """Drop all queued (not running) jobs."""
        for job in list(self._queue):
            if job.status == QUEUED:
                self._queue.remove(job)
                del self._jobs[job.id]
