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

import heapq
import itertools
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

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
        # Scheduling rounds elapsed.  Every round ages every skipped job by
        # exactly ``aging_step`` — a *uniform* shift — so the relative order
        # of queued jobs never changes between rounds.  The heap key
        # ``aging_step * born_round - priority`` is therefore time-invariant:
        # ordering by it is identical to ordering by
        # ``-(priority + aging_step * (round - born_round))`` at any round,
        # and the heap stays valid with no per-round updates.
        self._heap: List[Tuple[int, int, str]] = []  # (rank, seq, job_id)
        self._round = 0
        self._born: Dict[str, int] = {}  # job_id -> submission round
        self._live: set[str] = set()  # job ids still queued (lazy deletion)

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
        self._born[job_id] = self._round
        self._live.add(job_id)
        rank = self._aging_step * self._round - priority
        heapq.heappush(self._heap, (rank, seq, job_id))
        return job_id

    def _effective(self, job: Job) -> int:
        """Job's effective priority at the current round (lazy aging)."""
        born = self._born.get(job.id, self._round)
        return job.priority + self._aging_step * (self._round - born)

    def pending(self) -> List[Job]:
        """Queued jobs in the order they would run (best first)."""
        live = [j for j in self._queue if j.id in self._live]
        for job in live:
            job.effective_priority = self._effective(job)
        return sorted(live, key=lambda j: (-j.effective_priority, j.seq))

    def get(self, job_id: str) -> Job:
        """Return the job with ``job_id`` (any status)."""
        try:
            return self._jobs[job_id]
        except KeyError:
            raise KeyError(f"unknown job id {job_id!r}") from None

    def __len__(self) -> int:
        return len(self._live)

    def _pop_next(self) -> Optional[Job]:
        """Remove and return the best queued job, aging the skipped ones.

        Aging is applied lazily: every skipped job gains ``aging_step``
        effective priority per round, but since the gain is uniform the
        heap order (built on the time-invariant rank) already reflects it.
        Stale heap entries (cleared jobs) are skipped via ``_live``.
        """
        while self._heap:
            _rank, _seq, job_id = heapq.heappop(self._heap)
            if job_id not in self._live:
                continue  # stale entry: already popped or cleared
            job = self._jobs[job_id]
            self._live.discard(job_id)
            self._round += 1
            job.effective_priority = self._effective(job)
            # Amortised cleanup of the lazily-deleted _queue list.
            if len(self._queue) > 4 * len(self._live) + 64:
                self._queue = [j for j in self._queue if j.id in self._live]
            return job
        return None

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
            if job.id in self._live:
                self._queue.remove(job)
                del self._jobs[job.id]
                self._live.discard(job.id)
                self._born.pop(job.id, None)
