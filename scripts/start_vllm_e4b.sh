#!/usr/bin/env bash
# 값싼 단계(의도 분류·질문 다시쓰기·대화 요약·사용자 기억)가 쓰는 gemma4:e4b(google/gemma-4-E4B-it,
# BF16 15GB)를 vLLM으로 서빙한다. 2026-09-13까지 이 넷은 Ollama e4b로 갔는데, MOPAN의 로컬 모델을
# 전부 vLLM에 두기로 해서(소유자 지시) 26b(8001, GPU1)와 별개 프로세스로 GPU0 8002에 띄운다.
# vLLM은 프로세스 하나가 모델 하나라 서버가 둘이고, .env VLLM_BASE_URL에 둘을 쉼표로 적으면 백엔드가
# 각각 /v1/models로 이름을 발견해 그 이름의 요청을 그 서버로 보낸다(app/llm/catalog.py).
# 12%(≈22GB) = 가중치 15GB + 짧은 프롬프트용 KV. 임베딩 서버(8003, 25%)와 같은 GPU0.
set -euo pipefail
export VLLM_MODEL_PATH="${VLLM_MODEL_PATH:-$(cd "$(dirname "$0")/../.." && pwd)/models/gemma-4-E4B-it}"
export VLLM_SERVED_NAME=gemma4:e4b VLLM_PORT="${VLLM_PORT:-8002}" VLLM_GPU="${VLLM_GPU:-0}" \
       VLLM_GPU_UTIL="${VLLM_GPU_UTIL:-0.12}" VLLM_LOG_NAME=vllm-e4b
exec "$(dirname "$0")/start_vllm.sh"
