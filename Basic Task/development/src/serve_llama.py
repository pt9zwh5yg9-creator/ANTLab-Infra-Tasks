from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parents[1]
RECOVERY_DIR = BASE_DIR / "results" / "recovery"


def iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def expand_path(value: str) -> str:
    return os.path.expanduser(os.path.expandvars(value))


def build_command(config: dict) -> list[str]:
    binary = expand_path(os.environ.get("LLAMA_BIN", "~/llama.cpp/build/bin/llama-server"))
    model = expand_path(config["model"])
    command = [
        binary,
        "-m",
        model,
        "-ngl",
        str(config.get("n_gpu_layers", 99)),
        "-c",
        str(config["ctx_size"]),
        "-np",
        str(config["parallel"]),
        "-cram",
        str(config.get("cache_ram", 0)),
        "-fa",
        str(config.get("flash_attn", "on")),
        "-ctk",
        str(config.get("cache_type_k", "f16")),
        "-ctv",
        str(config.get("cache_type_v", "f16")),
        "-lv",
        str(config.get("verbosity", 4)),
        "--host",
        str(config.get("host", "0.0.0.0")),
        "--port",
        str(config.get("port", 8080)),
    ]
    if config.get("cont_batching", True):
        command.append("-cb")
    return command


def append_event(events_path: Path, state_path: Path, event: str, **fields) -> None:
    record = {"timestamp": iso_now(), "event": event, **fields}
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with events_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    state = {
        "updated_at": record["timestamp"],
        "last_event": record,
        "events_file": str(events_path),
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(record, ensure_ascii=False), flush=True)


def pump_output(process: subprocess.Popen, log_path: Path) -> None:
    with log_path.open("a", encoding="utf-8") as handle:
        if process.stdout is None:
            return
        for line in process.stdout:
            handle.write(line)
            handle.flush()
            print(line, end="", flush=True)


def health_ok(base_url: str) -> bool:
    try:
        response = httpx.get(f"{base_url}/health", timeout=2)
        return response.status_code == 200
    except Exception:
        return False


def wait_ready(base_url: str, timeout_s: float) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if health_ok(base_url):
            return True
        time.sleep(1)
    return False


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Supervise llama-server with health checks and restarts.")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--max-restarts", type=int, default=5)
    parser.add_argument("--ready-timeout", type=float, default=180.0)
    parser.add_argument("--backoff", type=float, default=2.0)
    parser.add_argument("--once", action="store_true", help="Do not restart after the first exit.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    events_path = RECOVERY_DIR / f"events-{stamp}.jsonl"
    state_path = RECOVERY_DIR / "supervisor-state.json"
    server_log = Path(expand_path("~/logs")) / f"supervisor-server-{stamp}.log"
    server_log.parent.mkdir(parents=True, exist_ok=True)

    port = int(config.get("port", 8080))
    base_url = f"http://127.0.0.1:{port}"
    command = build_command(config)
    append_event(
        events_path,
        state_path,
        "supervisor_start",
        command=command,
        config=config,
        server_log=str(server_log),
    )

    current: subprocess.Popen | None = None
    restart_count = 0
    try:
        while True:
            current = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            threading.Thread(
                target=pump_output,
                args=(current, server_log),
                daemon=True,
            ).start()
            append_event(
                events_path,
                state_path,
                "server_start",
                pid=current.pid,
                restart_count=restart_count,
            )

            ready = wait_ready(base_url, args.ready_timeout)
            append_event(
                events_path,
                state_path,
                "server_ready" if ready else "server_not_ready",
                pid=current.pid,
                ready=ready,
            )
            if not ready:
                stop_process(current)

            consecutive_failures = 0
            while current.poll() is None:
                time.sleep(2)
                if health_ok(base_url):
                    consecutive_failures = 0
                    continue
                consecutive_failures += 1
                append_event(
                    events_path,
                    state_path,
                    "health_check_failed",
                    pid=current.pid,
                    consecutive_failures=consecutive_failures,
                )
                if consecutive_failures >= 3:
                    append_event(
                        events_path,
                        state_path,
                        "restart_requested",
                        pid=current.pid,
                        reason="health_check_failed",
                    )
                    stop_process(current)
                    break

            exit_code = current.poll()
            append_event(
                events_path,
                state_path,
                "server_exit",
                pid=current.pid,
                exit_code=exit_code,
            )
            if args.once:
                append_event(events_path, state_path, "supervisor_stopped", reason="once")
                break
            if restart_count >= args.max_restarts:
                append_event(
                    events_path,
                    state_path,
                    "max_restarts_reached",
                    restart_count=restart_count,
                )
                break
            restart_count += 1
            append_event(
                events_path,
                state_path,
                "restart_scheduled",
                restart_count=restart_count,
                backoff_s=args.backoff,
            )
            time.sleep(args.backoff)
    except KeyboardInterrupt:
        append_event(events_path, state_path, "supervisor_interrupted")
    finally:
        if current is not None and current.poll() is None:
            stop_process(current)
            append_event(events_path, state_path, "server_stopped", pid=current.pid)


if __name__ == "__main__":
    main()
