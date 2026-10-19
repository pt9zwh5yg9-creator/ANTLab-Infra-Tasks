from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.models import RequestSpec
from src.priority import PriorityPolicy

RULES = Path(__file__).resolve().parents[1] / "configs" / "priority_rules.json"


def make_spec(prompt_id: str, task_type: str, mode: str, deadline_ms: int) -> RequestSpec:
    return RequestSpec(
        request_id=f"R-{prompt_id}",
        arrival_offset_ms=0,
        user_id="U01",
        session_id="s",
        prompt_id=prompt_id,
        task_type=task_type,
        interaction_mode=mode,
        deadline_after_arrival_ms=deadline_ms,
        suggested_output_budget_tokens=256,
        business_context="test",
    )


def main() -> None:
    policy = PriorityPolicy.load(RULES)
    live = policy.decide(make_spec("T01", "live_notice", "interactive", 15000))
    incident = policy.decide(
        make_spec("T03", "incident_analysis", "interactive", 45000)
    )
    document = policy.decide(
        make_spec("T04", "document_summary", "batch", 180000)
    )
    rewrite = policy.decide(make_spec("T08", "code_rewrite", "batch", 240000))

    assert live.label == "high", live
    assert incident.label == "high", incident
    assert document.label == "low", document
    assert rewrite.label == "low", rewrite
    assert live.score > incident.score > document.score

    aged = policy.decide(make_spec("T04", "document_summary", "batch", 180000), waited_s=60)
    assert aged.score > document.score, (aged, document)

    starved = policy.decide(
        make_spec("T04", "document_summary", "batch", 180000), waited_s=9
    )
    assert starved.score > document.score, (starved, document)
    assert aged.score >= starved.score, (aged, starved)
    assert starved.label == "high", starved
    print("priority tests passed")


if __name__ == "__main__":
    main()
