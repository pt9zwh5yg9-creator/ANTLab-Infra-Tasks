from __future__ import annotations

import json
from pathlib import Path

from .models import PriorityDecision, RequestSpec


class PriorityPolicy:
    def __init__(self, rules: dict):
        self.rules = rules

    @classmethod
    def load(cls, path: Path) -> "PriorityPolicy":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def base_score(self, spec: RequestSpec) -> tuple[int, list[str]]:
        mode = spec.interaction_mode.lower()
        score = int(
            self.rules["interactive_base"]
            if mode == "interactive"
            else self.rules["batch_base"]
        )
        reasons = [f"mode={mode}:{score}"]

        bonus = int(self.rules["task_bonus"].get(spec.task_type, 0))
        score += bonus
        reasons.append(f"task={spec.task_type}:{bonus:+d}")

        deadline_bonus = 0
        for rule in self.rules["deadline_bonus"]:
            if spec.deadline_after_arrival_ms <= int(rule["max_ms"]):
                deadline_bonus = int(rule["bonus"])
                break
        score += deadline_bonus
        reasons.append(f"deadline={spec.deadline_after_arrival_ms}ms:{deadline_bonus:+d}")
        return score, reasons

    def decide(self, spec: RequestSpec, waited_s: float = 0.0) -> PriorityDecision:
        base, reasons = self.base_score(spec)
        interval = max(1e-6, float(self.rules["aging_interval_s"]))
        aging = min(
            int(self.rules["aging_cap"]),
            int(max(0.0, waited_s) / interval) * int(self.rules["aging_bonus"]),
        )
        score = base + aging
        if aging:
            reasons.append(f"aging=+{aging}")

        starvation_bonus = 0
        threshold = float(self.rules.get("starvation_threshold_s", 0.0))
        if threshold > 0 and waited_s >= threshold:
            starvation_bonus = int(self.rules.get("starvation_bonus", 0))
            score += starvation_bonus
            reasons.append(f"starvation=+{starvation_bonus}")

        if score >= int(self.rules["high_threshold"]):
            label = "high"
        elif score >= int(self.rules["medium_threshold"]):
            label = "medium"
        else:
            label = "low"
        return PriorityDecision(score=score, label=label, reason="; ".join(reasons))
