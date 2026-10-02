#!/usr/bin/env bash
# 임베딩 모델(Qwen3-Embedding-8B, BF16 14GB)을 vLLM pooling 서버로 GPU 0의 8003에 띄운다. .env EMBEDDING_BASE_URL이 여기를 본다.
# 25%(≈46GB) = 가중치 14GB + 긴 배치용 여유. vLLM은 `dimensions`를 400으로 거절하므로(0.30에서도 같다) 프로바이더가 앞 1536차원을
# 잘라 정규화한다. 서버 판을 올려도 벡터는 같다 - 0.26과 0.30의 출력 코사인 0.9999(2026-10-03 실측), 다시 임베딩할 필요 없음.
set -euo pipefail
export VLLM_MODEL_PATH="${VLLM_EMBED_MODEL_PATH:-$(cd "$(dirname "$0")/../.." && pwd)/models/Qwen3-Embedding-8B}"
export VLLM_SERVED_NAME=qwen3-embedding:8b VLLM_PORT="${VLLM_EMBED_PORT:-8003}" VLLM_GPU="${VLLM_EMBED_GPU:-0}" \
       VLLM_GPU_UTIL="${VLLM_EMBED_GPU_UTIL:-0.25}" VLLM_LOG_NAME=vllm-embed VLLM_RUNNER=pooling \
       VLLM_MAX_MODEL_LEN=8192 VLLM_MAX_NUM_SEQS=128
exec "$(dirname "$0")/start_vllm.sh"
