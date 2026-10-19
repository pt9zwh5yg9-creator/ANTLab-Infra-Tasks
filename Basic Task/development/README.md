# 基础阶段开发说明

## 1. 项目简介

本项目为不超过 10 人的小型工作室搭建本地大模型推理服务，覆盖办公文档处理、会议资料整理、制度问答和辅助编程。服务使用 WSL2 + llama.cpp/llama-server + Qwen2.5-7B-Instruct Q4_K_M，在 NVIDIA RTX 5060 Laptop GPU 上完成推理。

系统包含：

- FastAPI 服务层：文件读取、资料与提示词组装、流式转发、请求 ID、错误返回
- llama-server 监督器：健康检查、异常记录、自动重启、就绪确认
- 固定负载测试程序：单并发/多并发、逐请求原始记录、汇总统计
- 前端：服务状态、运行记录、逐请求指标、模型输出、基线与优化对比、恢复记录
- 恢复测试：运行中杀掉模型进程，验证检测、重启、重试和恢复后请求

## 2. 场景与目标

场景为 8 人工作室，日常任务包括客户邮件、会议纪要、制度问答、代码审查和代码修复。正式测试使用 10 条固定任务：

- 4 条纯提示词任务
- 4 条关联办公文档任务
- 2 条关联 Python 源文件任务

优化目标为多用户并发时的首 token 延迟和吞吐。基线为 1 个 slot，优化后为 4 个 slot，并启用 Flash Attention 和 q8_0 KV。

## 3. 系统结构

```text
浏览器前端 / 固定负载测试程序
              │
              ▼
        FastAPI 服务层 :8000
  文件读取、提示词组装、流式转发、请求 ID
              │
              ▼
       llama-server :8080
              │
              ▼
  GPU：Qwen2.5-7B Q4_K_M + KV Cache

  serve_llama.py 监督器
  ├─ 启动/监控 llama-server
  ├─ 健康检查失败记录事件
  ├─ 自动重启并等待就绪
  └─ 写入 results/recovery/events-*.jsonl
```

主要模块：

| 文件 | 作用 |
| --- | --- |
| `src/app.py` | FastAPI 服务层、静态前端、运行/恢复记录 API |
| `src/runner.py` | 固定负载测试、逐请求 JSONL、汇总统计 |
| `src/serve_llama.py` | llama-server 监督器 |
| `src/recovery_test.py` | 杀掉服务、重试请求、验证恢复 |
| `src/static/` | 前端页面、样式和交互 |
| `configs/service.json` | 服务层系统消息、输出上限、采样参数 |
| `configs/workload.json` | 10 条固定任务和关联文件 |
| `configs/llama-baseline.json` | 基线 llama-server 配置 |
| `configs/llama-optimized.json` | 优化 llama-server 配置 |

## 4. 环境与模型

| 项目 | 配置 |
| --- | --- |
| GPU | NVIDIA GeForce RTX 5060 Laptop GPU，8151 MiB 专用显存 |
| 驱动 | 596.56；驱动报告 CUDA 13.2 |
| CPU | Intel Core Ultra 7 251HX，WSL 内 18 线程 |
| 系统 | Windows 11 + WSL2 Ubuntu 24.04，内核 6.18.54.1-microsoft-standard-WSL2 |
| CUDA Toolkit | 13.2.86 |
| llama.cpp | v0.7.0，commit `620d2225cc436ab0b001e3200e590e3b286ef9ab` |
| 模型 | Qwen2.5-7B-Instruct，GGUF Q4_K_M，7.6B |
| 模型文件 | `qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf` + `...00002-of-00002.gguf` |
| 模型 SHA256 | `dfce12e3862a5283ccfb88221b48480e58745165de856439950d0f22590580db`；`539cf93f78e887edea1c04e2d7d8cdaca9d01dae9c9025bcb8accbe29df3d72a` |
| 基线配置 | ctx 8192，1 slot，F16 KV，Flash Attention off |
| 优化配置 | ctx 32768，4 slots，每 slot 8192，q8_0 KV，Flash Attention on |

8 GB 显存下，7B Q4_K_M 权重约 4.17 GiB，是能兼顾中文办公和代码任务的较稳妥选择。14B 及以上模型在该显存下难以同时保留足够的 KV 和并发 slot。

模型下载来源为 ModelScope 的 `Qwen/Qwen2.5-7B-Instruct-GGUF`，使用两个分片，llama-server 加载第一个分片时会自动加载第二个。模型权重未提交到仓库。

## 5. 目录结构

```text
Basic Task/development/
├─ README.md
├─ requirements.txt
├─ src/
│  ├─ app.py
│  ├─ runner.py
│  ├─ serve_llama.py
│  ├─ recovery_test.py
│  └─ static/
├─ configs/
│  ├─ service.json
│  ├─ workload.json
│  ├─ llama-baseline.json
│  └─ llama-optimized.json
├─ results/
│  ├─ runs/
│  └─ recovery/
└─ reports/
   └─ 基础阶段考核报告.md
```

## 6. 运行方法

以下命令在 WSL Ubuntu 中执行，项目目录以 `~/ANTLab-Infra-Tasks/Basic Task/development` 为例。

### 6.1 启动模型服务

优化配置：

```bash
cd ~/ANTLab-Infra-Tasks/"Basic Task/development"
source ~/ANTLab-Infra-Tasks/.venv/bin/activate
python -m src.serve_llama --config configs/llama-optimized.json
```

基线配置：

```bash
python -m src.serve_llama --config configs/llama-baseline.json
```

监督器会输出 `server_start` 和 `server_ready` 事件，并将事件写入 `results/recovery/events-*.jsonl`。

### 6.2 启动 FastAPI

另开窗口：

```bash
cd ~/ANTLab-Infra-Tasks/"Basic Task/development"
source ~/ANTLab-Infra-Tasks/.venv/bin/activate
uvicorn src.app:app --host 0.0.0.0 --port 8000
```

### 6.3 打开前端

浏览器访问：

```text
http://localhost:8000
```

### 6.4 运行固定负载

单并发：

```bash
python -m src.runner --run-id baseline_c1_r1 --concurrency 1
```

多并发：

```bash
python -m src.runner --run-id optimized_c4_r1 --concurrency 4
```

每次运行生成：

```text
results/runs/<run_id>/
├─ meta.json
├─ requests.jsonl
├─ outputs/
└─ summary.json
```

### 6.5 运行恢复测试

先确保监督器和 FastAPI 都在运行，然后：

```bash
python -m src.recovery_test \
  --run-id recovery_r1 \
  --tasks P01,D01,C02,P02 \
  --concurrency 2 \
  --retries 8 \
  --retry-delay 2 \
  --kill-after 4
```

结果位于 `results/recovery/recovery_r1/`，前端“恢复记录”标签会读取该结果。

## 7. 关键指标口径

- TTFT：客户端提交到第一个非空 `delta.content` 的时间，包含排队、提示词处理和传输。
- TPOT：`(完成时间 - 首 token 时间) / max(1, 输出 token 数 - 1)`，排除首 token。
- 完成耗时：客户端提交到流结束。
- 吞吐：一次运行的总输出 token 数除以第一次提交到最后一次完成的墙钟时间。
- P95：对一次运行内的请求耗时做最近秩 P95；三轮结果在报告中给均值和范围。
- 输入 token：最终 `timings.cache_n + timings.prompt_n`。
- 输出 token：最终 `timings.predicted_n`。

## 8. 结果索引

- 基线单并发：`results/runs/baseline_c1_r1..r3`
- 基线多并发：`results/runs/baseline_c4_r1..r3`
- 优化单并发：`results/runs/optimized_c1_r1..r3`
- 优化多并发：`results/runs/optimized_c4_r1..r3`
- 恢复测试：`results/recovery/recovery_r1`
- 监督器事件：`results/recovery/events-*.jsonl`
- 报告：`reports/基础阶段考核报告.md`

## 9. 已知限制

- 8 GB 显存限制了模型规模和并发上限；4 并发是本次验证的配置，未测试 10 并发。
- 优化同时改了 slot 数、上下文、Flash Attention 和 KV 精度，单变量归因有限；报告按配置整体对比，并列出各改动的预期作用和资源证据。
- 正式三轮之间没有重启 llama-server；`-cram 0` 关闭了主机 RAM prompt cache，但 slot KV 状态会保留。冷启动单独记录，冒烟轮不计入正式统计。
- 当前输出质量仍有失败样例，尤其是 P02、P03、C01、C02；报告第 8 节列出具体影响。
- 服务层重试在测试程序中实现；生产环境若需要严格的请求幂等，应在服务层增加响应缓存或幂等键。

## 10. AI 辅助开发说明

开发过程中使用 Codex/GPT-5 辅助生成项目骨架、测试程序、前端和文档草稿，并由本人在 WSL + RTX 5060 环境中实际运行、核对日志、检查输出和修正配置。AI 未参与推理服务的运行时调用链；模型推理由本地 llama-server 完成。报告中所有性能数字均来自 `results/` 下的原始记录。
