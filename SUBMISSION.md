# ANTLab Infra Tasks Submission

姓名：金琪凯

GitHub 仓库：https://github.com/pt9zwh5yg9-creator/ANTLab-Infra-Tasks

提交分支：`basic-task`

功能与结果提交：`83a15bc`（最终 HEAD 以仓库 `basic-task` 分支为准）

## Basic Task

| 内容 | 路径 |
| --- | --- |
| 开发说明 | `Basic Task/development/README.md` |
| 书面报告 | `Basic Task/development/reports/基础阶段考核报告.md` |
| 源码 | `Basic Task/development/src/` |
| 配置 | `Basic Task/development/configs/` |
| 测试 | `Basic Task/development/tests/` |
| 逐请求原始数据 | `Basic Task/development/results/runs/` |
| 恢复测试 | `Basic Task/development/results/recovery/` |
| 启动日志 | `Basic Task/development/results/logs/` |

运行入口：

```bash
cd ~/ANTLab-Infra-Tasks/"Basic Task/development"
source ~/ANTLab-Infra-Tasks/.venv/bin/activate
python -m src.serve_llama --config configs/llama-optimized.json
```

另开窗口：

```bash
cd ~/ANTLab-Infra-Tasks/"Basic Task/development"
source ~/ANTLab-Infra-Tasks/.venv/bin/activate
uvicorn src.app:app --host 0.0.0.0 --port 8000
```

浏览器打开 `http://localhost:8000` 查看监控面板，打开 `http://localhost:8000/chat` 直接对话。

## Advanced Task

| 内容 | 路径 |
| --- | --- |
| 开发说明 | `Advanced Task/development/README.md` |
| 书面报告 | `Advanced Task/development/reports/进阶阶段考核报告.md` |
| 调度器 | `Advanced Task/development/src/scheduler.py` |
| 优先级规则 | `Advanced Task/development/src/priority.py`、`configs/priority_rules.json` |
| 回放程序 | `Advanced Task/development/src/replay.py` |
| 单元测试 | `Advanced Task/development/tests/` |
| FIFO 结果 | `Advanced Task/development/results/fifo_real_r1..r3` |
| priority + reservation 结果 | `Advanced Task/development/results/priority_reserved_real_r1..r3` |
| 图表 | `Advanced Task/development/reports/figures/` |

运行入口：

```bash
cd ~/ANTLab-Infra-Tasks/"Advanced Task/development"
source ~/ANTLab-Infra-Tasks/.venv/bin/activate
python -m tests.test_priority
python -m tests.test_scheduler
python -m src.replay --run-id fifo_real_r1 --policy fifo --backend llama --slots 2
python -m src.replay --run-id priority_reserved_real_r1 --policy priority --backend llama --slots 2 --batch-limit 1
```

## 关键结果

- Basic：4 并发 TTFT 中位数 40.44 s → 0.73 s，吞吐 53.97 → 86.73 tokens/s，显存 5375 → 5400 MiB。
- Basic 恢复：运行中杀掉 llama-server，12.756 s 恢复，4/4 逻辑请求最终成功。
- Advanced：交互任务期限达成率 4.17% → 70.83%，整体期限达成率 52.08% → 85.42%；吞吐 67.43 → 63.22 tokens/s，后台等待上升。
