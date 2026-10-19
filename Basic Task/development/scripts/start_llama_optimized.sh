#!/usr/bin/env bash
set -euo pipefail

MODEL="${MODEL:-$HOME/models/Qwen2.5-7B-Instruct-GGUF/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf}"
BIN="${LLAMA_BIN:-$HOME/llama.cpp/build/bin/llama-server}"
LOG_DIR="${LOG_DIR:-$HOME/logs}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/llama-optimized-$(date +%Y%m%d-%H%M%S).log"

echo "Logging to $LOG"
"$BIN" \
  -m "$MODEL" \
  -ngl 99 \
  -c 32768 \
  -np 4 \
  -cb \
  -fa on \
  -ctk q8_0 \
  -ctv q8_0 \
  -cram 0 \
  -lv 4 \
  --host 0.0.0.0 \
  --port 8080 2>&1 | tee "$LOG"
