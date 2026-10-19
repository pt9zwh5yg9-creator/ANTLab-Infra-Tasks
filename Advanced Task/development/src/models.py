from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class RequestSpec:
    request_id: str
    arrival_offset_ms: int
    user_id: str
    session_id: str
    prompt_id: str
    task_type: str
    interaction_mode: str
    deadline_after_arrival_ms: int
    suggested_output_budget_tokens: int
    business_context: str
    prompt: str = ""
    document: str | None = None
    document_sha256: str | None = None
    order: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RequestSpec":
        return cls(
            request_id=str(data["request_id"]),
            arrival_offset_ms=int(data.get("arrival_offset_ms", 0)),
            user_id=str(data.get("user_id", "")),
            session_id=str(data.get("session_id", "")),
            prompt_id=str(data["prompt_id"]),
            task_type=str(data.get("task_type", "")),
            interaction_mode=str(data.get("interaction_mode", "batch")),
            deadline_after_arrival_ms=int(data.get("deadline_after_arrival_ms", 0)),
            suggested_output_budget_tokens=int(
                data.get("suggested_output_budget_tokens", 1024)
            ),
            business_context=str(data.get("business_context", "")),
        )


@dataclass
class PriorityDecision:
    score: int
    label: str
    reason: str


@dataclass
class RequestRun:
    spec: RequestSpec
    sequence: int
    priority: PriorityDecision
    base_priority: PriorityDecision | None
    arrival: float
    enqueue: float
    dispatch: float | None = None
    first_token: float | None = None
    completion: float | None = None
    dispatch_order: int | None = None
    dispatch_priority: PriorityDecision | None = None
    status: str = "queued"
    output_text: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
