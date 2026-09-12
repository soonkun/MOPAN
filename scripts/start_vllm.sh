#!/usr/bin/env bash
# vLLM으로 답변 모델(gemma4:26b = google/gemma-4-26B-A4B-it BF16)을 서빙한다.
# 근거와 수치: docs/local-llm-concurrency.md. Ollama는 e4b(값싼 단계)·임베딩용으로 그대로 둔다.
#   GPU1 한 장, 총 183GB의 42%(≈77GB)를 선점: 가중치 ≈48GB + KV 캐시. Ollama 모델과 같은 GPU에
#   있으므로 이 비율을 올리면 기동 시 "메모리 부족"으로 죽는다 - nvidia-smi로 free를 보고 정한다.
#   --served-model-name gemma4:26b : MOPAN 카탈로그·화면·추적 기록이 쓰는 이름 그대로.
#   --reasoning-parser/--tool-call-parser gemma4 : 사고 토큰과 함수 호출을 본문에서 분리(vLLM 0.26).
#   --enable-prefix-caching : 시스템 프롬프트(모든 요청의 접두사) KV 재사용.
#   --max-model-len 16384 : MOPAN 프롬프트 상한(예산 8k + 필수 2k + 출력)에 여유. 262k를 다 열면 KV만 먹는다.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"   # …/soonkun
MODEL="${VLLM_MODEL_PATH:-$ROOT/models/gemma-4-26B-A4B-it}"
PORT="${VLLM_PORT:-8001}"
GPU="${VLLM_GPU:-1}"
UTIL="${VLLM_GPU_UTIL:-0.42}"
LOG="$ROOT/MOPAN/logs/vllm.log"
mkdir -p "$(dirname "$LOG")"

if curl -s -m 3 -o /dev/null "http://127.0.0.1:$PORT/v1/models"; then
  echo "vllm already listening on $PORT"; exit 0
fi
CUDA_VISIBLE_DEVICES="$GPU" HF_HUB_OFFLINE=1 VLLM_LOGGING_LEVEL=INFO \
  setsid nohup "$ROOT/opt/vllm/.venv/bin/vllm" serve "$MODEL" \
    --served-model-name gemma4:26b \
    --host 127.0.0.1 --port "$PORT" \
    --dtype bfloat16 \
    --max-model-len 16384 --max-num-seqs 64 \
    --gpu-memory-utilization "$UTIL" \
    --enable-prefix-caching \
    --reasoning-parser gemma4 \
    --enable-auto-tool-choice --tool-call-parser gemma4 \
    --limit-mm-per-prompt '{"image": 5}' \
    >> "$LOG" 2>&1 < /dev/null &
echo "vllm starting (pid $!), log: $LOG"
