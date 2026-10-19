from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .backend import LlamaBackend, MockBackend
from .models import RequestSpec
from .priority import PriorityPolicy
from .scheduler import Scheduler

ADVANCED_DIR = Path(__file__).resolve().parents[1]
TASK_DIR = ADVANCED_DIR.parent
DEFAULT_REQUESTS = TASK_DIR / "参考测试（仅作参考）" / "参考请求序列.jsonl"
DEFAULT_PROMPTS = TASK_DIR / "参考测试（仅作参考）" / "测试提示词.md"
DEFAULT_DOCS = TASK_DIR / "参考测试（仅作参考）" / "输入文档"
DEFAULT_RULES = ADVANCED_DIR / "configs" / "priority_rules.json"
DEFAULT_OUTPUT = ADVANCED_DIR / "results"

DOCUMENTS = {
    "T04": "工作室项目知识库-v1.md",
    "T05": "工作室项目知识库-v1.md",
    "T06": "工作室项目知识库-v2.md",
    "T07": "batch_export.py",
    "T08": "batch_export.py",
}


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_prompts(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^### (T\d+)｜[^\n]*\n\n(.*?)(?=\n### |\n## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    prompts: dict[str, str] = {}
    for match in pattern.finditer(text):
        body = match.group(2).strip()
        body = re.sub(r"^关联文件：.*?\n\n", "", body, flags=re.DOTALL)
        prompts[match.group(1)] = body
    return prompts


def load_specs(
    path: Path, prompts: dict[str, str], docs_dir: Path
) -> list[RequestSpec]:
    specs: list[RequestSpec] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        spec = RequestSpec.from_dict(json.loads(line))
        spec.order = index
        spec.prompt = prompts.get(spec.prompt_id, "")
        if not spec.prompt:
            raise ValueError(f"missing prompt for {spec.prompt_id}")
        document_name = DOCUMENTS.get(spec.prompt_id)
        if document_name:
            document_path = docs_dir / document_name
            spec.document = document_path.read_text(encoding="utf-8")
            spec.document_sha256 = sha256_text(spec.document)
        specs.append(spec)
    return specs


def percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(ratio * len(ordered)) - 1)
    return ordered[min(index, len(ordered) - 1)]


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def group_stats(records: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for record in records:
        name = str(record.get(key, ""))
        group = groups.setdefault(
            name,
            {
                "total": 0,
                "success": 0,
                "deadline_met": 0,
                "wait_s": [],
                "ttft_s": [],
            },
        )
        group["total"] += 1
        if record["status"] == "success":
            group["success"] += 1
        if record.get("deadline_met"):
            group["deadline_met"] += 1
        if record.get("wait_s") is not None:
            group["wait_s"].append(record["wait_s"])
        if record.get("ttft_s") is not None:
            group["ttft_s"].append(record["ttft_s"])

    for group in groups.values():
        waits = group.pop("wait_s")
        ttfts = group.pop("ttft_s")
        group["wait_avg_s"] = mean(waits)
        group["wait_p95_s"] = percentile(waits, 0.95)
        group["ttft_avg_s"] = mean(ttfts)
        group["ttft_p95_s"] = percentile(ttfts, 0.95)
        group["deadline_rate"] = (
            group["deadline_met"] / group["success"] if group["success"] else None
        )
    return groups


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [record for record in records if record["status"] == "success"]
    waits = [record["wait_s"] for record in successful if record.get("wait_s") is not None]
    ttfts = [record["ttft_s"] for record in successful if record.get("ttft_s") is not None]
    completions = [
        record["completion_s"]
        for record in successful
        if record.get("completion_s") is not None
    ]
    low_waits = [
        record["wait_s"]
        for record in successful
        if record.get("priority_label") == "low" and record.get("wait_s") is not None
    ]
    output_tokens = sum(record.get("output_tokens") or 0 for record in successful)
    arrival_epochs = [record["arrival_epoch"] for record in records]
    completion_epochs = [
        record["completion_epoch"]
        for record in records
        if record.get("completion_epoch") is not None
    ]
    window_s = (
        max(completion_epochs) - min(arrival_epochs)
        if arrival_epochs and completion_epochs
        else 0.0
    )
    deadline_met = sum(1 for record in successful if record.get("deadline_met"))

    return {
        "total": len(records),
        "success": len(successful),
        "failed": sum(1 for record in records if record["status"] != "success"),
        "deadline_met": deadline_met,
        "deadline_rate": (deadline_met / len(successful)) if successful else None,
        "wait_avg_s": mean(waits),
        "wait_p95_s": percentile(waits, 0.95),
        "ttft_avg_s": mean(ttfts),
        "ttft_p95_s": percentile(ttfts, 0.95),
        "completion_avg_s": mean(completions),
        "completion_p95_s": percentile(completions, 0.95),
        "low_priority_max_wait_s": max(low_waits) if low_waits else None,
        "output_tokens": output_tokens,
        "window_s": window_s,
        "throughput_tokens_per_s": (output_tokens / window_s) if window_s else None,
        "by_task_type": group_stats(records, "task_type"),
        "by_interaction_mode": group_stats(records, "interaction_mode"),
        "by_priority": group_stats(records, "priority_label"),
    }


async def run_replay(args: argparse.Namespace) -> Path:
    prompts = load_prompts(args.prompts)
    specs = load_specs(args.requests, prompts, args.docs_dir)
    policy = PriorityPolicy.load(args.rules)
    backend = (
        MockBackend()
        if args.backend == "mock"
        else LlamaBackend(base_url=args.base_url, timeout=args.timeout)
    )
    scheduler = Scheduler(
        policy,
        backend,
        slots=args.slots,
        mode=args.policy,
        aging_interval=args.aging_interval,
        batch_limit=args.batch_limit if args.batch_limit > 0 else None,
    )

    run_dir = args.output_root / args.run_id
    (run_dir / "outputs").mkdir(parents=True, exist_ok=True)
    started_at = iso_now()
    start_epoch = time.time()
    start = time.monotonic()
    await scheduler.start()

    async def deliver(spec: RequestSpec) -> None:
        target = start + spec.arrival_offset_ms / 1000.0
        delay = target - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        await scheduler.submit(spec)

    await asyncio.gather(*(deliver(spec) for spec in specs))
    await scheduler.wait()
    await scheduler.stop()

    records: list[dict[str, Any]] = []
    for run in scheduler.runs:
        output_path = run_dir / "outputs" / f"{run.spec.request_id}.txt"
        output_path.write_text(run.output_text, encoding="utf-8")
        base_decision = run.base_priority or run.priority
        decision = run.dispatch_priority or run.priority
        wait_s = run.dispatch - run.arrival if run.dispatch is not None else None
        ttft_s = run.first_token - run.arrival if run.first_token is not None else None
        completion_s = (
            run.completion - run.arrival if run.completion is not None else None
        )
        deadline_met = (
            completion_s is not None
            and completion_s * 1000.0 <= run.spec.deadline_after_arrival_ms
        )
        arrival_epoch = start_epoch + (run.arrival - start)
        completion_epoch = (
            start_epoch + (run.completion - start)
            if run.completion is not None
            else None
        )
        records.append(
            {
                "request_id": run.spec.request_id,
                "prompt_id": run.spec.prompt_id,
                "user_id": run.spec.user_id,
                "session_id": run.spec.session_id,
                "task_type": run.spec.task_type,
                "interaction_mode": run.spec.interaction_mode,
                "deadline_after_arrival_ms": run.spec.deadline_after_arrival_ms,
                "suggested_output_budget_tokens": run.spec.suggested_output_budget_tokens,
                "business_context": run.spec.business_context,
                "document_sha256": run.spec.document_sha256,
                "priority_score": decision.score,
                "priority_label": base_decision.label,
                "effective_priority_label": decision.label,
                "starvation_boosted": decision.label != base_decision.label,
                "priority_reason": decision.reason,
                "dispatch_order": run.dispatch_order,
                "status": run.status,
                "error": run.error,
                "wait_s": wait_s,
                "ttft_s": ttft_s,
                "completion_s": completion_s,
                "deadline_met": deadline_met,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "arrival_epoch": arrival_epoch,
                "completion_epoch": completion_epoch,
                "output_path": f"outputs/{run.spec.request_id}.txt",
            }
        )

    records.sort(key=lambda record: record["dispatch_order"] or 0)
    with (run_dir / "requests.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = summarize(records)
    summary.update(
        {
            "run_id": args.run_id,
            "policy": args.policy,
            "backend": args.backend,
            "slots": args.slots,
            "aging_interval_s": args.aging_interval,
            "batch_limit": args.batch_limit if args.batch_limit > 0 else None,
            "started_at": started_at,
            "completed_at": iso_now(),
            "requests_path": str(args.requests),
            "prompts_path": str(args.prompts),
            "rules_path": str(args.rules),
        }
    )
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return run_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay the Advanced Task request sequence.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--policy", choices=["fifo", "priority"], default="priority")
    parser.add_argument("--backend", choices=["mock", "llama"], default="mock")
    parser.add_argument("--slots", type=int, default=4)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--requests", type=Path, default=DEFAULT_REQUESTS)
    parser.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS)
    parser.add_argument("--docs-dir", type=Path, default=DEFAULT_DOCS)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--aging-interval", type=float, default=5.0)
    parser.add_argument(
        "--batch-limit",
        type=int,
        default=0,
        help="Maximum concurrent batch requests; 0 means unlimited.",
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = asyncio.run(run_replay(args))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nReplay directory: {run_dir}")


if __name__ == "__main__":
    main()
