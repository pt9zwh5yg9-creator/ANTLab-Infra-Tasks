from __future__ import annotations

import asyncio
import json
import time

import httpx

from .models import RequestRun


class MockBackend:
    """Deterministic backend for scheduler tests."""

    def __init__(self, prompt_s: float = 0.15, token_s: float = 0.0015):
        self.prompt_s = prompt_s
        self.token_s = token_s

    async def generate(self, run: RequestRun) -> None:
        await asyncio.sleep(self.prompt_s)
        run.first_token = time.monotonic()
        tokens = max(1, run.spec.suggested_output_budget_tokens)
        await asyncio.sleep(tokens * self.token_s)
        run.output_tokens = tokens
        run.input_tokens = len(run.spec.prompt) // 4
        run.output_text = f"[mock] {run.spec.request_id} {run.spec.prompt_id}"


class LlamaBackend:
    """Streaming client to a local llama-server OpenAI-compatible endpoint."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        system_prompt: str = (
            "你为小型工作室处理办公文档和编程任务。按照任务要求输出，"
            "将资料事实、待确认信息与建议分别表述。以本次提供的文档版本为依据，"
            "代码任务提供可检查的实现。"
        ),
        timeout: float = 300.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.system_prompt = system_prompt
        self.timeout = timeout

    async def generate(self, run: RequestRun) -> None:
        content = run.spec.prompt
        if run.spec.document:
            content = f"资料：\n{run.spec.document}\n\n任务：\n{content}"
        payload = {
            "model": "local",
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": content},
            ],
            "max_tokens": run.spec.suggested_output_budget_tokens,
            "temperature": 0.2,
            "top_p": 0.9,
            "seed": 42,
            "stream": True,
        }
        timings = None
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/chat/completions",
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    event = json.loads(data)
                    if event.get("error"):
                        raise RuntimeError(str(event["error"]))
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    chunk = delta.get("content")
                    if chunk:
                        if run.first_token is None:
                            run.first_token = time.monotonic()
                        run.output_text += chunk
                    if choices[0].get("finish_reason") is not None:
                        timings = event.get("timings")
        if run.first_token is None:
            raise RuntimeError("no content token received")
        if timings:
            run.input_tokens = (timings.get("cache_n") or 0) + (
                timings.get("prompt_n") or 0
            )
            run.output_tokens = timings.get("predicted_n")
