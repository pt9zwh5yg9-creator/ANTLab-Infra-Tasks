from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from src.runner import BASIC_DIR, DEFAULT_WORKLOAD, TASK_DIR, iso_now, sha256_file

RECOVERY_DIR = BASIC_DIR / "results" / "recovery"


def find_server_pids() -> list[int]:
    try:
        result = subprocess.run(
            ["pgrep", "-af", "llama-server"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        if "--port 8080" not in line:
            continue
        try:
            pids.append(int(line.split(maxsplit=1)[0]))
        except (ValueError, IndexError):
            continue
    return sorted(set(pids))


def append_event(path: Path, event: str, **fields: Any) -> None:
    record = {"timestamp": iso_now(), "event": event, **fields}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


async def wait_for_health(url: str, timeout_s: float) -> float | None:
    started = time.time()
    while time.time() - started < timeout_s:
        try:
            response = httpx.get(url, timeout=2)
            if response.status_code == 200:
                return time.time() - started
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return None


async def kill_server_after(
    delay_s: float,
    events_path: Path,
    health_url: str,
    timeout_s: float,
) -> float | None:
    await asyncio.sleep(delay_s)
    pids = find_server_pids()
    append_event(events_path, "kill_requested", delay_s=delay_s, pids=pids)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
            append_event(events_path, "kill_sent", pid=pid, signal="SIGKILL")
        except ProcessLookupError:
            append_event(events_path, "kill_skipped", pid=pid, reason="not_found")
    recovery_s = await wait_for_health(health_url, timeout_s)
    append_event(events_path, "recovery_health_ok", recovery_s=recovery_s)
    return recovery_s


async def send_attempt(
    client: httpx.AsyncClient,
    task: dict[str, Any],
    request_id: str,
    attempt: int,
    args: argparse.Namespace,
) -> dict[str, Any]:
    file_path = None
    file_sha256 = None
    document = None
    if task.get("file"):
        file_path = TASK_DIR / "提示词与输入文档" / task["file"]
        file_sha256 = sha256_file(file_path)
        document = file_path.read_text(encoding="utf-8")

    payload = {
        "prompt": task["prompt"],
        "document": document,
        "max_tokens": task.get("max_tokens", 1024),
        "request_id": request_id,
    }

    started = time.perf_counter()
    submitted_at = iso_now()
    first_token_perf: float | None = None
    first_token_at: str | None = None
    completed_perf: float | None = None
    completed_at: str | None = None
    content_parts: list[str] = []
    timings: dict[str, Any] | None = None
    done = False
    error_message: str | None = None
    status = "failed"
    http_status: int | None = None

    try:
        async with client.stream(
            "POST",
            f"{args.base_url.rstrip('/')}/v1/chat/stream",
            json=payload,
            timeout=args.timeout,
        ) as response:
            http_status = response.status_code
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_text = line[6:].strip()
                if data_text == "[DONE]":
                    done = True
                    break
                try:
                    event = json.loads(data_text)
                except json.JSONDecodeError:
                    continue
                if event.get("error"):
                    error_message = str(event["error"])
                    break
                choices = event.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content")
                    if content:
                        if first_token_perf is None:
                            first_token_perf = time.perf_counter()
                            first_token_at = iso_now()
                        content_parts.append(content)
                    if choices[0].get("finish_reason") is not None:
                        timings = event.get("timings")
        if done:
            status = "success"
        else:
            status = "interrupted"
            error_message = error_message or "stream ended without [DONE]"
    except Exception as exc:
        status = "interrupted" if first_token_perf is not None else "failed"
        error_message = f"{type(exc).__name__}: {exc}"
    finally:
        completed_perf = time.perf_counter()
        completed_at = iso_now()

    latency_s = completed_perf - started
    ttft_s = (
        (first_token_perf - started) if first_token_perf is not None else None
    )
    output_tokens = timings.get("predicted_n") if timings else None
    input_tokens = (
        (timings.get("cache_n") or 0) + (timings.get("prompt_n") or 0)
        if timings
        else None
    )
    return {
        "attempt": attempt,
        "request_id": request_id,
        "prompt_id": task["prompt_id"],
        "file": task.get("file"),
        "file_sha256": file_sha256,
        "submitted_at": submitted_at,
        "first_token_at": first_token_at,
        "completed_at": completed_at,
        "ttft_s": ttft_s,
        "latency_s": latency_s,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "http_status": http_status,
        "status": status,
        "error_message": error_message,
        "partial_output_chars": sum(len(part) for part in content_parts),
        "output_text": "".join(content_parts),
    }


async def run_task(
    client: httpx.AsyncClient,
    task: dict[str, Any],
    index: int,
    args: argparse.Namespace,
    semaphore: asyncio.Semaphore,
    run_dir: Path,
) -> dict[str, Any]:
    async with semaphore:
        request_id = f"{args.run_id}-{task['prompt_id']}-{uuid.uuid4().hex[:6]}"
        attempts: list[dict[str, Any]] = []
        for attempt in range(1, args.retries + 2):
            result = await send_attempt(client, task, request_id, attempt, args)
            attempts.append(result)
            if result["status"] == "success":
                break
            if attempt <= args.retries:
                await asyncio.sleep(args.retry_delay)

        final = attempts[-1]
        output_path = run_dir / "outputs" / f"{request_id}.txt"
        output_path.write_text(final.get("output_text", ""), encoding="utf-8")
        return {
            "request_index": index,
            "request_id": request_id,
            "prompt_id": task["prompt_id"],
            "status": final["status"],
            "attempt_count": len(attempts),
            "attempts": [
                {key: value for key, value in attempt.items() if key != "output_text"}
                for attempt in attempts
            ],
            "ttft_s": final.get("ttft_s"),
            "latency_s": final.get("latency_s"),
            "input_tokens": final.get("input_tokens"),
            "output_tokens": final.get("output_tokens"),
            "output_path": f"outputs/{request_id}.txt",
        }


async def run_recovery(args: argparse.Namespace) -> Path:
    workload = json.loads(args.workload.read_text(encoding="utf-8"))
    tasks = workload["tasks"]
    if args.tasks:
        selected = {item.strip() for item in args.tasks.split(",") if item.strip()}
        tasks = [task for task in tasks if task["prompt_id"] in selected]
    if not tasks:
        raise SystemExit("No tasks selected.")

    run_dir = RECOVERY_DIR / args.run_id
    (run_dir / "outputs").mkdir(parents=True, exist_ok=True)
    events_path = run_dir / "events.jsonl"
    started_at = iso_now()

    semaphore = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient() as client:
        kill_task = asyncio.create_task(
            kill_server_after(
                args.kill_after,
                events_path,
                "http://127.0.0.1:8080/health",
                args.recovery_timeout,
            )
        )
        jobs = [
            run_task(client, task, index, args, semaphore, run_dir)
            for index, task in enumerate(tasks)
        ]
        records = list(await asyncio.gather(*jobs))
        recovery_s = await kill_task

        verification_id = f"{args.run_id}-verify-{uuid.uuid4().hex[:6]}"
        verification = await send_attempt(
            client,
            {
                "prompt_id": "VERIFY",
                "prompt": "用一句话确认服务已经恢复。",
                "task_type": "office",
                "file": None,
                "max_tokens": 64,
            },
            verification_id,
            1,
            args,
        )
        (run_dir / "outputs" / f"{verification_id}.txt").write_text(
            verification.get("output_text", ""), encoding="utf-8"
        )

    with (run_dir / "requests.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = {
        "run_id": args.run_id,
        "started_at": started_at,
        "completed_at": iso_now(),
        "total": len(records),
        "success": sum(1 for record in records if record["status"] == "success"),
        "failed": sum(1 for record in records if record["status"] != "success"),
        "retries": sum(record["attempt_count"] - 1 for record in records),
        "interrupted_attempts": sum(
            1
            for record in records
            for attempt in record["attempts"]
            if attempt["status"] == "interrupted"
        ),
        "recovery_s": recovery_s,
        "verification_status": verification["status"],
        "verification_request_id": verification_id,
        "tasks": [task["prompt_id"] for task in tasks],
        "concurrency": args.concurrency,
        "retries_limit": args.retries,
        "workload_sha256": sha256_file(args.workload),
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return run_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Kill llama-server during a request batch and verify recovery.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--workload", type=Path, default=DEFAULT_WORKLOAD)
    parser.add_argument("--tasks", default="P01,D01,C02,P02")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--retries", type=int, default=6)
    parser.add_argument("--retry-delay", type=float, default=2.0)
    parser.add_argument("--kill-after", type=float, default=4.0)
    parser.add_argument("--recovery-timeout", type=float, default=90.0)
    parser.add_argument("--timeout", type=float, default=300.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = asyncio.run(run_recovery(args))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nRecovery run directory: {run_dir}")


if __name__ == "__main__":
    main()
