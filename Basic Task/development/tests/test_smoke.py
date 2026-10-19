from __future__ import annotations

from pathlib import Path

import httpx

BASE_URL = "http://127.0.0.1:8000"
BASIC_DIR = Path(__file__).resolve().parents[1]
TASK_DIR = BASIC_DIR.parent
SAMPLE_FILE = TASK_DIR / "提示词与输入文档" / "输入文档" / "办公输入01-项目会议与进度资料.md"


def main() -> None:
    with httpx.Client(timeout=120) as client:
        health = client.get(f"{BASE_URL}/health")
        health.raise_for_status()
        assert health.json()["status"] == "ok", health.text

        chat = client.post(
            f"{BASE_URL}/v1/chat",
            json={"prompt": "用一句话介绍你自己", "max_tokens": 32},
        )
        chat.raise_for_status()
        chat_data = chat.json()
        assert chat_data["request_id"], chat_data
        content = chat_data["response"]["choices"][0]["message"]["content"]
        assert content.strip(), chat_data

        sample = SAMPLE_FILE.read_bytes()
        file_chat = client.post(
            f"{BASE_URL}/v1/chat/file",
            data={"prompt": "用一句话概括这份资料的主题。"},
            files={"file": (SAMPLE_FILE.name, sample, "text/markdown")},
        )
        file_chat.raise_for_status()
        file_data = file_chat.json()
        assert file_data["request_id"], file_data
        assert file_data["response"]["choices"][0]["message"]["content"].strip()

        runs = client.get(f"{BASE_URL}/api/runs")
        runs.raise_for_status()
        assert "runs" in runs.json()

    print("smoke tests passed")


if __name__ == "__main__":
    main()
