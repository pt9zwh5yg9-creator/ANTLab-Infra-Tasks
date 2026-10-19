from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG = json.loads((BASE_DIR / "configs" / "service.json").read_text(encoding="utf-8"))
STATIC_DIR = Path(__file__).resolve().parent / "static"
RUNS_DIR = BASE_DIR / "results" / "runs"
RECOVERY_DIR = BASE_DIR / "results" / "recovery"

app = FastAPI(title="ANTLab Basic Inference Service", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ChatRequest(BaseModel):
    prompt: str
    document: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    request_id: str | None = None


def new_request_id() -> str:
    return f"req-{uuid.uuid4().hex[:12]}"


def build_payload(req: ChatRequest) -> dict[str, Any]:
    messages = [{"role": "system", "content": CONFIG["system_prompt"]}]
    if req.document:
        content = f"资料：\n{req.document}\n\n任务：\n{req.prompt}"
    else:
        content = req.prompt
    messages.append({"role": "user", "content": content})
    return {
        "model": CONFIG["model"],
        "messages": messages,
        "max_tokens": req.max_tokens or CONFIG["max_tokens"],
        "temperature": CONFIG["temperature"] if req.temperature is None else req.temperature,
        "top_p": CONFIG["top_p"],
        "seed": CONFIG.get("seed", 42),
    }


async def call_llama(payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{CONFIG['llama_base_url']}/v1/chat/completions"
    async with httpx.AsyncClient(timeout=CONFIG["request_timeout_s"]) as client:
        try:
            response = await client.post(url, json={**payload, "stream": False})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"llama-server error: {exc}") from exc
    return response.json()


@app.get("/health")
async def health() -> dict[str, Any]:
    url = f"{CONFIG['llama_base_url']}/health"
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            response = await client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail=f"llama-server unavailable: {exc}") from exc
    return {"status": "ok", "llama": response.json()}


def safe_run_dir(run_id: str) -> Path:
    if not run_id or "/" in run_id or "\\" in run_id or ".." in run_id:
        raise HTTPException(status_code=400, detail="invalid run id")
    run_dir = RUNS_DIR / run_id
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="run not found")
    return run_dir


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/status")
async def service_status() -> dict[str, Any]:
    llama_ok = True
    llama_detail: Any = None
    try:
        llama_detail = await health()
    except HTTPException as exc:
        llama_ok = False
        llama_detail = exc.detail

    gpu = ""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,memory.free",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        gpu = (result.stdout or result.stderr).strip()
    except Exception as exc:
        gpu = f"unavailable: {exc}"

    return {
        "service": "ok",
        "llama": llama_detail,
        "llama_ok": llama_ok,
        "gpu": gpu,
        "model": CONFIG.get("model"),
    }


@app.get("/api/runs")
async def list_runs() -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    if RUNS_DIR.is_dir():
        directories = sorted(
            (path for path in RUNS_DIR.iterdir() if path.is_dir()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for run_dir in directories:
            summary_path = run_dir / "summary.json"
            if not summary_path.is_file():
                continue
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            meta_path = run_dir / "meta.json"
            meta = (
                json.loads(meta_path.read_text(encoding="utf-8"))
                if meta_path.is_file()
                else {}
            )
            runs.append(
                {
                    "run_id": run_dir.name,
                    "summary": summary,
                    "meta": {
                        "started_at": meta.get("started_at"),
                        "concurrency": meta.get("concurrency"),
                        "repeats": meta.get("repeats"),
                        "tasks": meta.get("tasks"),
                        "platform": meta.get("platform"),
                        "nvidia_smi": meta.get("nvidia_smi"),
                    },
                }
            )
    return {"runs": runs}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> dict[str, Any]:
    run_dir = safe_run_dir(run_id)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    records = [
        json.loads(line)
        for line in (run_dir / "requests.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return {"run_id": run_id, "summary": summary, "meta": meta, "records": records}


@app.get("/api/runs/{run_id}/outputs/{request_id}")
async def get_output(run_id: str, request_id: str) -> dict[str, Any]:
    run_dir = safe_run_dir(run_id)
    if not request_id or "/" in request_id or "\\" in request_id or ".." in request_id:
        raise HTTPException(status_code=400, detail="invalid request id")
    path = run_dir / "outputs" / f"{request_id}.txt"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="output not found")
    return {
        "run_id": run_id,
        "request_id": request_id,
        "content": path.read_text(encoding="utf-8"),
    }


@app.get("/api/recovery")
async def recovery_status() -> dict[str, Any]:
    state: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []

    state_path = RECOVERY_DIR / "supervisor-state.json"
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))

    event_files = sorted(
        RECOVERY_DIR.glob("events-*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if event_files:
        lines = event_files[0].read_text(encoding="utf-8").splitlines()
        for line in lines[-200:]:
            if line.strip():
                events.append(json.loads(line))

    test_dirs = sorted(
        (path for path in RECOVERY_DIR.iterdir() if path.is_dir())
        if RECOVERY_DIR.is_dir()
        else [],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for test_dir in test_dirs:
        summary_path = test_dir / "summary.json"
        if summary_path.is_file():
            tests.append(json.loads(summary_path.read_text(encoding="utf-8")))

    return {"state": state, "events": events, "tests": tests}


@app.post("/v1/chat")
async def chat(req: ChatRequest) -> dict[str, Any]:
    request_id = req.request_id or new_request_id()
    started = time.perf_counter()
    data = await call_llama(build_payload(req))
    elapsed = time.perf_counter() - started
    return {"request_id": request_id, "elapsed_s": elapsed, "response": data}


@app.post("/v1/chat/file")
async def chat_file(
    prompt: str = Form(...),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    raw = await file.read()
    try:
        document = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="file must be UTF-8") from exc
    req = ChatRequest(prompt=prompt, document=document)
    return await chat(req)


@app.post("/v1/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    request_id = req.request_id or new_request_id()
    payload = build_payload(req)
    url = f"{CONFIG['llama_base_url']}/v1/chat/completions"

    async def event_stream():
        yield f"data: {json.dumps({'request_id': request_id}, ensure_ascii=False)}\n\n"
        async with httpx.AsyncClient(timeout=CONFIG["request_timeout_s"]) as client:
            try:
                async with client.stream(
                    "POST", url, json={**payload, "stream": True}
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line:
                            yield f"{line}\n\n"
            except httpx.HTTPError as exc:
                error = {"error": f"llama-server error: {exc}"}
                yield f"data: {json.dumps(error, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
