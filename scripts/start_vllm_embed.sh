#!/usr/bin/env bash
# vLLM으로 임베딩 모델(qwen3-embedding:8b = Qwen/Qwen3-Embedding-8B BF16)을 GPU 0에 서빙한다.
# 근거: docs/local-llm-concurrency.md §6, 기술 보고서 §15. Ollama 임베딩(4 슬롯)이 색인 병목이었다.
#   --served-model-name qwen3-embedding:8b : EMBEDDING_MODEL·프로필·카탈로그 이름 그대로.
#   --runner pooling : /v1/embeddings 서버. --max-model-len 8192는 청크 상한(4,095토큰)의 두 배.
#   차원 1536은 앱이 요청마다 dimensions로 보내고, 서버가 무시하면 프로바이더가 앞 1536차원을
#   잘라 정규화한다(MRL) - Ollama의 dimensions와 같은 연산.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"   # …/soonkun
MODEL="${VLLM_EMBED_MODEL_PATH:-$ROOT/models/Qwen3-Embedding-8B}"
PORT="${VLLM_EMBED_PORT:-8003}"
GPU="${VLLM_EMBED_GPU:-0}"
UTIL="${VLLM_EMBED_GPU_UTIL:-0.25}"
LOG="$ROOT/MOPAN/logs/vllm-embed.log"
mkdir -p "$(dirname "$LOG")"
if curl -s -m 3 -o /dev/null "http://127.0.0.1:$PORT/v1/models"; then
  echo "vllm-embed already listening on $PORT"; exit 0
fi
CUDA_VISIBLE_DEVICES="$GPU" HF_HUB_OFFLINE=1 \
  setsid nohup "$ROOT/opt/vllm/.venv/bin/vllm" serve "$MODEL" \
    --runner pooling \
    --served-model-name qwen3-embedding:8b \
    --host 127.0.0.1 --port "$PORT" \
    --dtype bfloat16 --max-model-len 8192 --max-num-seqs 128 \
    --gpu-memory-utilization "$UTIL" \
    >> "$LOG" 2>&1 < /dev/null &
echo "vllm-embed starting (pid $!), log: $LOG"
