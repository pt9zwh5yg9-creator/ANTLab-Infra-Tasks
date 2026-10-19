from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import platform
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

BASIC_DIR = Path(__file__).resolve().parents[1]
TASK_DIR = BASIC_DIR.parent
DEFAULT_WORKLOAD = BASIC_DIR / "configs" / "workload.json"
DEFAULT_OUTPUT = BASIC_DIR / "results" / "runs"
SERVICE_CONFIG = BASIC_DIR / "configs" / "service.json"


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_command(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return (result.stdout or result.stderr).strip()
    except Exception as exc:
        return f"ERROR: {exc}"


def percentile(values: list[float], ratio: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(ratio * len(ordered)) - 1)
    return ordered[min(index, len(ordered) - 1)]


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [record for record in records if record["status"] == "success"]
    ttfts = [record["ttft_s"] for record in successful if record.get("ttft_s") is not None]
    latencies = [
        record["latency_s"] for record in successful if record.get("latency_s") is not None
    ]
    tpots = [record["tpot_s"] for record in successful if record.get("tpot_s") is not None]
    output_tokens = sum(
        record.get("output_tokens") or 0 for record in successful
    )

    submitted = [record["submitted_epoch"] for record in records]
    completed = [
        record["completed_epoch"]
        for record in records
        if record.get("completed_epoch") is not None
    ]
    window_s = (max(completed) - min(submitted)) if submitted and completed else 0.0

    by_prompt: dict[str, dict[str, Any]] = {}
    for record in records:
        group = by_prompt.setdefault(
            record["prompt_id"],
            {"total": 0, "success": 0, "failed": 0, "timeout": 0, "ttft_s": []},
        )
        group["total"] += 1
        group[record["status"]] = group.get(record["status"], 0) + 1
        if record.get("ttft_s") is not None:
            group["ttft_s"].append(record["ttft_s"])

    for group in by_prompt.values():
        values = group.pop("ttft_s")
        group["ttft_median_s"] = percentile(values, 0.5)
        group["ttft_p95_s"] = percentile(values, 0.95)

    return {
        "total": len(records),
        "success": len(successful),
        "failed": sum(1 for record in records if record["status"] == "failed"),
        "timeout": sum(1 for record in records if record["status"] == "timeout"),
        "ttft_median_s": percentile(ttfts, 0.5),
        "ttft_p95_s": percentile(ttfts, 0.95),
        "latency_median_s": percentile(latencies, 0.5),
        "latency_p95_s": percentile(latencies, 0.95),
        "tpot_median_s": percentile(tpots, 0.5),
        "output_tokens": output_tokens,
        "window_s": window_s,
        "throughput_tokens_per_s": (output_tokens / window_s) if window_s > 0 else None,
        "by_prompt": by_prompt,
    }


async def run_one(
    client: httpx.AsyncClient,
    task: dict[str, Any],
    index: int,
    repeat: int,
    args: argparse.Namespace,
    semaphore: asyncio.Semaphore,
    run_dir: Path,
) -> dict[str, Any]:
    await asyncio.sleep(index * args.interval_ms / 1000.0)

    async with semaphore:
        request_id = (
            f"{args.run_id}-{task['prompt_id']}-r{repeat + 1}-{uuid.uuid4().hex[:6]}"
        )
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

        submitted_epoch = time.time()
        started = time.perf_counter()
        submitted_at = iso_now()
        first_token_at: str | None = None
        first_token_perf: float | None = None
        completed_perf: float | None = None
        completed_epoch: float | None = None
        completed_at: str | None = None
        status = "success"
        error_type: str | None = None
        error_message: str | None = None
        http_status: int | None = None
        timings: dict[str, Any] | None = None
        content_parts: list[str] = []

        url = f"{args.base_url.rstrip('/')}/v1/chat/stream"
        try:
            async with client.stream("POST", url, json=payload) as response:
                http_status = response.status_code
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_text = line[6:].strip()
                    if data_text == "[DONE]":
                        break
                    try:
                        event = json.loads(data_text)
                    except json.JSONDecodeError:
                        continue
                    if "request_id" in event and "choices" not in event:
                        request_id = event["request_id"]
                        continue
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
        except httpx.TimeoutException as exc:
            status = "timeout"
            error_type = "timeout"
            error_message = str(exc)
        except httpx.HTTPStatusError as exc:
            status = "failed"
            error_type = "http"
            error_message = str(exc)
        except Exception as exc:
            status = "failed"
            error_type = type(exc).__name__
            error_message = str(exc)
        finally:
            completed_perf = time.perf_counter()
            completed_epoch = time.time()
            completed_at = iso_now()

        finished = completed_perf if completed_perf is not None else time.perf_counter()
        latency_s = finished - started
        ttft_s = (first_token_perf - started) if first_token_perf is not None else None
        input_tokens = None
        output_tokens = None
        tpot_s = None
        if timings:
            input_tokens = (timings.get("cache_n") or 0) + (timings.get("prompt_n") or 0)
            output_tokens = timings.get("predicted_n")
        if first_token_perf is not None and output_tokens:
            tpot_s = (finished - first_token_perf) / max(1, output_tokens - 1)

        output_text = "".join(content_parts)
        output_rel = f"outputs/{request_id}.txt"
        output_path = run_dir / output_rel
        output_path.write_text(output_text, encoding="utf-8")

        return {
            "run_id": args.run_id,
            "request_index": index,
            "repeat": repeat + 1,
            "request_id": request_id,
            "prompt_id": task["prompt_id"],
            "task_type": task["task_type"],
            "file": task.get("file"),
            "file_sha256": file_sha256,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "max_tokens": payload["max_tokens"],
            "status": status,
            "error_type": error_type,
            "error_message": error_message,
            "http_status": http_status,
            "submitted_at": submitted_at,
            "first_token_at": first_token_at,
            "completed_at": completed_at,
            "submitted_epoch": submitted_epoch,
            "completed_epoch": completed_epoch,
            "ttft_s": ttft_s,
            "tpot_s": tpot_s,
            "latency_s": latency_s,
            "output_path": output_rel,
            "output_preview": output_text[:200],
            "timings": timings,
        }


async def run_benchmark(args: argparse.Namespace) -> Path:
    workload = json.loads(args.workload.read_text(encoding="utf-8"))
    tasks = workload["tasks"]
    if args.tasks:
        selected = {item.strip() for item in args.tasks.split(",") if item.strip()}
        tasks = [task for task in tasks if task["prompt_id"] in selected]
    if not tasks:
        raise SystemExit("No tasks selected.")

    run_dir = args.output_root / args.run_id
    (run_dir / "outputs").mkdir(parents=True, exist_ok=True)

    service_config = json.loads(SERVICE_CONFIG.read_text(encoding="utf-8"))
    meta = {
        "run_id": args.run_id,
        "started_at": iso_now(),
        "base_url": args.base_url,
        "concurrency": args.concurrency,
        "repeats": args.repeats,
        "interval_ms": args.interval_ms,
        "tasks": [task["prompt_id"] for task in tasks],
        "workload_path": str(args.workload),
        "workload_sha256": sha256_file(args.workload),
        "service_config": service_config,
        "platform": platform.platform(),
        "python": sys.version,
        "uname": run_command(["uname", "-a"]),
        "nvidia_smi": run_command(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,driver_version",
                "--format=csv,noheader",
            ]
        ),
    }
    (run_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    semaphore = asyncio.Semaphore(args.concurrency)
    records: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        jobs = []
        index = 0
        for repeat in range(args.repeats):
            for task in tasks:
                jobs.append(
                    run_one(
                        client,
                        task,
                        index,
                        repeat,
                        args,
                        semaphore,
                        run_dir,
                    )
                )
                index += 1
        records = list(await asyncio.gather(*jobs))

    records.sort(key=lambda record: record["request_index"])
    with (run_dir / "requests.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = summarize(records)
    summary["completed_at"] = iso_now()
    (run_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return run_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the fixed ANTLab workload.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--workload", type=Path, default=DEFAULT_WORKLOAD)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--interval-ms", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--tasks", default="", help="Comma-separated prompt IDs, e.g. P01,D01")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_dir = asyncio.run(run_benchmark(args))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nRun directory: {run_dir}")


if __name__ == "__main__":
    main()
