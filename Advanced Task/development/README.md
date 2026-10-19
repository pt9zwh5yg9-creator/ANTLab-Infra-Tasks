# Advanced Task：请求优先级判断与调度优化

## 1. 项目简介

本项目在基础阶段的本地推理服务前增加一个请求调度层，对比 FIFO 和“优先级 + 交互预留”两种策略。场景为 7 人工作室：客户演示、故障排查和制度问答属于交互任务，文档整理、代码审查和代码改造属于后台任务。后台任务先到达时，FIFO 会让交互任务排在长任务后面，TTFT 和期限达成率明显变差。

最终策略：

- 规则优先级：交互模式、任务类型、期限共同决定基础分。
- 等待老化：每等待 10 秒加 1 分，最高加 30 分。
- 饥饿保护：等待超过 8 秒获得一次强提升。
- 交互预留：`batch_limit=1`，后台任务最多同时占 1 个 slot，交互任务可使用全部 2 个调度 slot。

## 2. 结果摘要

2 个调度 slot，16 条固定请求，FIFO 与 priority + reservation 各 3 轮：

| 指标 | FIFO | priority + reservation | 变化 |
| --- | ---: | ---: | ---: |
| 整体期限达成率 | 52.08% | 85.42% | +33.3 pp |
| 交互任务期限达成率 | 4.17% | 70.83% | +66.7 pp |
| 交互任务平均等待 | 57.36 s | 13.75 s | -76.0% |
| 交互任务 P95 等待 | 93.37 s | 29.02 s | -68.9% |
| 整体平均等待 | 53.31 s | 42.42 s | -20.4% |
| 平均完成耗时 | 69.23 s | 54.36 s | -21.5% |
| 吞吐 | 67.43 tok/s | 63.22 tok/s | -6.3% |
| 后台任务平均等待 | 49.27 s | 71.10 s | +44.3% |
| 低优先级最大等待 | 105.88 s | 121.63 s | +14.9% |

取舍：交互任务的等待和期限达成显著改善，整体平均等待和完成耗时也下降；代价是后台任务等待变长、吞吐略降。后台任务在 180–240 秒期限内仍然全部完成。

## 3. 目录

```text
Advanced Task/development/
├─ README.md
├─ src/
│  ├─ models.py
│  ├─ priority.py
│  ├─ scheduler.py
│  ├─ backend.py
│  └─ replay.py
├─ configs/priority_rules.json
├─ tests/
│  ├─ test_priority.py
│  └─ test_scheduler.py
├─ results/
└─ reports/
   ├─ 进阶阶段考核报告.md
   ├─ 演示清单.md
   └─ figures/
```

## 4. 运行

### 4.1 单元测试

```bash
cd ~/ANTLab-Infra-Tasks/"Advanced Task/development"
source ~/ANTLab-Infra-Tasks/.venv/bin/activate
python -m tests.test_priority
python -m tests.test_scheduler
```

### 4.2 mock 回放

```bash
python -m src.replay --run-id mock_fifo --policy fifo --backend mock --slots 2
python -m src.replay --run-id mock_priority --policy priority --backend mock --slots 2 --batch-limit 1
```

### 4.3 真实模型回放

先确保 `llama-server` 在 8080 端口可用：

```bash
curl -s http://127.0.0.1:8080/health
```

FIFO 基线：

```bash
python -m src.replay --run-id fifo_real_r1 --policy fifo --backend llama --slots 2
```

优先级 + 交互预留：

```bash
python -m src.replay --run-id priority_reserved_real_r1 --policy priority --backend llama --slots 2 --batch-limit 1
```

### 4.4 查看结果

```bash
cat results/priority_reserved_real_r1/summary.json
head -n 1 results/priority_reserved_real_r1/requests.jsonl
```

## 5. 指标口径

- 等待：从请求到达到获得调度 slot。
- TTFT：从请求到达到第一个非空内容 token。
- 完成耗时：从请求到达到流结束。
- 期限达成：完成耗时不超过 `deadline_after_arrival_ms`。
- 吞吐：总输出 token 数除以首请求到达到最后完成的墙钟窗口。
- 优先级判断耗时：规则计算为微秒级，未单独计入端到端；不调用外部决策模型。

## 6. 已知限制

- 调度器不能抢占正在生成的后台请求；只能影响等待队列和后续 slot 分配。
- 交互预留会增加后台等待和尾延迟，适合“交互优先”场景，不适合纯吞吐场景。
- 4 个 slot 且负载较轻时，FIFO 和 priority 几乎没有差别；调度只在拥塞时有价值。
- 调度器状态当前保存在进程内；进程重启后队列不会恢复。基础阶段的服务恢复机制可继续用于 llama-server，但调度队列持久化尚未实现。
