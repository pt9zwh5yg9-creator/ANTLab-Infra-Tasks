from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.backend import MockBackend
from src.models import RequestSpec
from src.priority import PriorityPolicy
from src.scheduler import Scheduler

RULES = Path(__file__).resolve().parents[1] / "configs" / "priority_rules.json"


def spec(request_id: str, prompt_id: str, task_type: str, mode: str, deadline: int) -> RequestSpec:
    return RequestSpec(
        request_id=request_id,
        arrival_offset_ms=0,
        user_id="U01",
        session_id="s",
        prompt_id=prompt_id,
        task_type=task_type,
        interaction_mode=mode,
        deadline_after_arrival_ms=deadline,
        suggested_output_budget_tokens=32,
        business_context="scheduler test",
    )


async def order_for(mode: str) -> list[str]:
    policy = PriorityPolicy.load(RULES)
    scheduler = Scheduler(
        policy,
        MockBackend(prompt_s=0.01, token_s=0.0001),
        slots=1,
        mode=mode,
    )
    await scheduler.submit(
        spec("low", "T04", "document_summary", "batch", 180000)
    )
    await scheduler.submit(
        spec("high", "T01", "live_notice", "interactive", 15000)
    )
    await scheduler.start()
    await scheduler.wait()
    await scheduler.stop()
    return [
        run.spec.request_id
        for run in sorted(scheduler.runs, key=lambda item: item.dispatch_order or 0)
    ]


async def reservation_order() -> list[str]:
    policy = PriorityPolicy.load(RULES)
    scheduler = Scheduler(
        policy,
        MockBackend(prompt_s=0.02, token_s=0.0001),
        slots=2,
        mode="priority",
        batch_limit=1,
    )
    await scheduler.submit(spec("batch1", "T04", "document_summary", "batch", 180000))
    await scheduler.submit(spec("batch2", "T04", "document_summary", "batch", 180000))
    await scheduler.submit(spec("interactive", "T01", "live_notice", "interactive", 15000))
    await scheduler.start()
    await scheduler.wait()
    await scheduler.stop()
    return [
        run.spec.request_id
        for run in sorted(scheduler.runs, key=lambda item: item.dispatch_order or 0)
    ]


def main() -> None:
    fifo = asyncio.run(order_for("fifo"))
    priority = asyncio.run(order_for("priority"))
    reservation = asyncio.run(reservation_order())
    assert fifo == ["low", "high"], fifo
    assert priority == ["high", "low"], priority
    assert set(reservation[:2]) == {"batch1", "interactive"}, reservation
    assert reservation[-1] == "batch2", reservation
    print("scheduler tests passed")


if __name__ == "__main__":
    main()
