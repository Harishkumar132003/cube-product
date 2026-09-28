"""Background generation jobs, streamed to the UI over SSE.

Generation takes roughly ten seconds per cube, which is far too long to hold a
request open: the browser or any proxy in between will time out and the user is
left not knowing whether work is still happening. So the request starts a task
and returns an id, and progress is streamed separately.

The registry is in-process and in-memory. That is deliberate for now -- it
means a backend restart loses running jobs -- but it is the piece to replace
first when this runs on more than one worker.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

log = logging.getLogger(__name__)

JobState = Literal["running", "done", "failed"]
StepState = Literal["running", "done", "failed"]

#: How long a finished job stays readable, so a reconnecting browser can still
#: collect the result it missed.
RETAIN_SECONDS = 30 * 60


@dataclass
class Event:
    """One progress line. `key` identifies the step so the UI can update in
    place rather than appending a second row when a step finishes."""

    key: str
    label: str
    state: StepState
    detail: str | None = None
    at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "state": self.state,
            "detail": self.detail,
            "at": self.at,
        }


@dataclass
class Job:
    id: str
    project_id: str
    state: JobState = "running"
    events: list[Event] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    _queues: list[asyncio.Queue[Event | None]] = field(default_factory=list)

    def emit(
        self,
        key: str,
        label: str,
        state: StepState = "running",
        detail: str | None = None,
    ) -> None:
        event = Event(key=key, label=label, state=state, detail=detail)
        self.events.append(event)
        for queue in self._queues:
            queue.put_nowait(event)

    def _close(self) -> None:
        for queue in self._queues:
            queue.put_nowait(None)

    def subscribe(self) -> asyncio.Queue[Event | None]:
        """Attach a listener, replaying what it missed.

        A browser cannot open the stream until after the POST returns, so the
        first events always predate the first subscriber. Replaying from the
        log rather than only forwarding live events is what stops the modal
        opening blank on a fast step.
        """
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        for event in self.events:
            queue.put_nowait(event)
        if self.state != "running":
            queue.put_nowait(None)
        else:
            self._queues.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Event | None]) -> None:
        if queue in self._queues:
            self._queues.remove(queue)

    def steps(self) -> list[Event]:
        """The log collapsed to one row per step, latest state winning.

        `events` is append-only, so a step that started and finished appears
        twice. Anything rendering a list of steps wants this instead: a dict
        keyed on `key` keeps each step at the position it first appeared while
        replacing its state in place.
        """
        latest: dict[str, Event] = {}
        for event in self.events:
            latest[event.key] = event
        return list(latest.values())

    def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "state": self.state,
            "events": [e.as_dict() for e in self.steps()],
            "result": self.result,
            "error": self.error,
        }


_JOBS: dict[str, Job] = {}


def _evict() -> None:
    cutoff = time.time() - RETAIN_SECONDS
    stale = [
        job_id
        for job_id, job in _JOBS.items()
        if job.finished_at is not None and job.finished_at < cutoff
    ]
    for job_id in stale:
        del _JOBS[job_id]


def get(job_id: str) -> Job | None:
    return _JOBS.get(job_id)


def start(project_id: str, work: Callable[[Job], Awaitable[dict[str, Any]]]) -> Job:
    """Run `work` in the background, reporting progress through the job."""
    _evict()
    job = Job(id=str(uuid.uuid4()), project_id=project_id)
    _JOBS[job.id] = job

    async def run() -> None:
        try:
            job.result = await work(job)
            job.state = "done"
        except Exception as exc:  # noqa: BLE001 - the message is shown to the user
            log.exception("generation job %s failed", job.id)
            job.state = "failed"
            job.error = str(exc) or exc.__class__.__name__
            # Close off whatever was still in flight, so the UI does not leave a
            # step spinning forever next to an error message. Emitting rather
            # than mutating keeps the log append-only, and the steps collapse
            # over it correctly either way.
            for step in job.steps():
                if step.state == "running":
                    job.emit(step.key, step.label, "failed", "did not finish")
            job.emit("failed", "Generation failed", "failed", job.error)
        finally:
            job.finished_at = time.time()
            job._close()

    task = asyncio.create_task(run())
    # Hold a reference, or the event loop may garbage-collect the task mid-run.
    job_task_refs.add(task)
    task.add_done_callback(job_task_refs.discard)
    return job


job_task_refs: set[asyncio.Task[None]] = set()
