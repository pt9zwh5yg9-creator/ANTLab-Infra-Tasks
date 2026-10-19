from __future__ import annotations

import asyncio
import heapq
import itertools
import time

from .models import RequestRun, RequestSpec
from .priority import PriorityPolicy


class Scheduler:
    def __init__(
        self,
        policy: PriorityPolicy,
        backend,
        slots: int = 4,
        mode: str = "priority",
        aging_interval: float = 5.0,
        batch_limit: int | None = None,
    ):
        if mode not in {"fifo", "priority"}:
            raise ValueError("mode must be 'fifo' or 'priority'")
        self.policy = policy
        self.backend = backend
        self.slots = max(1, int(slots))
        self.mode = mode
        self.aging_interval = aging_interval
        self.batch_limit = batch_limit
        self.batch_running = 0

        self.queue: list[tuple[int, int, RequestRun]] = []
        self.condition = asyncio.Condition()
        self.semaphore = asyncio.Semaphore(self.slots)
        self.sequence = itertools.count(1)
        self.expected = 0
        self.completed = 0
        self.dispatch_counter = 0
        self.runs: list[RequestRun] = []
        self.closed = False
        self.dispatcher: asyncio.Task | None = None
        self.ager: asyncio.Task | None = None
        self.running_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        self.closed = False
        self.dispatcher = asyncio.create_task(self._dispatch_loop())
        self.ager = asyncio.create_task(self._aging_loop())

    async def submit(self, spec: RequestSpec) -> RequestRun:
        now = time.monotonic()
        base_priority = self.policy.decide(spec, 0.0)
        run = RequestRun(
            spec=spec,
            sequence=spec.order if spec.order > 0 else next(self.sequence),
            priority=base_priority,
            base_priority=base_priority,
            arrival=now,
            enqueue=now,
        )
        self.runs.append(run)
        self.expected += 1
        async with self.condition:
            heapq.heappush(self.queue, self._key(run, now))
            self.condition.notify_all()
        return run

    def _key(self, run: RequestRun, now: float) -> tuple[int, int, RequestRun]:
        if self.mode == "priority":
            decision = self.policy.decide(
                run.spec, max(0.0, now - run.enqueue)
            )
            run.priority = decision
            return (-decision.score, run.sequence, run)
        return (0, run.sequence, run)

    async def _dispatch_loop(self) -> None:
        while not self.closed:
            async with self.condition:
                while not self.queue and not self.closed:
                    await self.condition.wait()
                if self.closed and not self.queue:
                    return

            await self.semaphore.acquire()
            async with self.condition:
                if not self.queue:
                    self.semaphore.release()
                    continue
                if self.mode == "priority":
                    now = time.monotonic()
                    queued = [item[2] for item in self.queue]
                    self.queue = [self._key(run, now) for run in queued]
                    heapq.heapify(self.queue)
                selected: RequestRun | None = None
                skipped: list[tuple[int, int, RequestRun]] = []
                while self.queue:
                    item = heapq.heappop(self.queue)
                    candidate = item[2]
                    if (
                        self.batch_limit is not None
                        and candidate.spec.interaction_mode == "batch"
                        and self.batch_running >= self.batch_limit
                    ):
                        skipped.append(item)
                        continue
                    selected = candidate
                    break
                for item in skipped:
                    heapq.heappush(self.queue, item)
                if selected is None:
                    self.semaphore.release()
                    try:
                        await asyncio.wait_for(self.condition.wait(), timeout=0.5)
                    except asyncio.TimeoutError:
                        pass
                    continue
                run = selected
                if run.spec.interaction_mode == "batch":
                    self.batch_running += 1
            run.dispatch = time.monotonic()
            run.dispatch_priority = run.priority
            self.dispatch_counter += 1
            run.dispatch_order = self.dispatch_counter
            run.status = "running"
            task = asyncio.create_task(self._execute(run))
            self.running_tasks.add(task)
            task.add_done_callback(self.running_tasks.discard)

    async def _execute(self, run: RequestRun) -> None:
        try:
            await self.backend.generate(run)
            run.status = "success"
        except Exception as exc:
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
        finally:
            run.completion = time.monotonic()
            self.completed += 1
            self.semaphore.release()
            async with self.condition:
                if (
                    self.batch_limit is not None
                    and run.spec.interaction_mode == "batch"
                ):
                    self.batch_running = max(0, self.batch_running - 1)
                self.condition.notify_all()

    async def _aging_loop(self) -> None:
        while not self.closed:
            await asyncio.sleep(self.aging_interval)
            if self.mode != "priority":
                continue
            async with self.condition:
                if not self.queue:
                    continue
                queued = [item[2] for item in self.queue]
                now = time.monotonic()
                self.queue = [self._key(run, now) for run in queued]
                heapq.heapify(self.queue)
                self.condition.notify_all()

    async def wait(self) -> None:
        async with self.condition:
            while self.completed < self.expected:
                await self.condition.wait()

    async def stop(self) -> None:
        self.closed = True
        async with self.condition:
            self.condition.notify_all()
        for task in (self.dispatcher, self.ager):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *[task for task in (self.dispatcher, self.ager) if task is not None],
            return_exceptions=True,
        )
