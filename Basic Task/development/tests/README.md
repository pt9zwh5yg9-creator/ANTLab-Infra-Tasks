# 功能测试

先启动 `llama-server` 和 FastAPI（8000 端口），然后在 `Basic Task/development` 目录执行：

```bash
source ~/ANTLab-Infra-Tasks/.venv/bin/activate
python tests/test_smoke.py
```

测试内容：

- `/health` 同时检查 FastAPI 和 llama-server
- `/v1/chat` 纯提示词请求
- `/v1/chat/file` 读取一条 UTF-8 办公资料
- `/api/runs` 能列出已有运行记录

异常恢复测试不在这里重复，使用：

```bash
python -m src.recovery_test --run-id recovery_r1 --tasks P01,D01,C02,P02 --concurrency 2 --retries 8 --retry-delay 2 --kill-after 4
```
