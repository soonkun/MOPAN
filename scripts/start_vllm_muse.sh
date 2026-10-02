#!/usr/bin/env bash
# Meta Muse Glimmer 30B(Apache 2.0, BF16 60GB, 이미지 입력, 창 131k)를 GPU 1의 8006에 띄운다. 2026-10-03 소유자 요청으로 추가.
# vLLM 0.28 이상이 필요하다(파서 muse_glimmer - 추론 채널과 XML식 도구 호출을 본문에서 분리, 둘을 함께 써야 한다).
# 40%(≈73GB) = 가중치 60GB + KV. 같은 GPU 1의 gemma4:26b(42%)와 합쳐 82%.
# 모델 권장 샘플링은 temperature 1.0 / top_p 0.95 / top_k 64(요청이 값을 안 주면 generation_config.json의 이 값이 쓰인다).
set -euo pipefail
export VLLM_MODEL_PATH="${VLLM_MODEL_PATH:-$(cd "$(dirname "$0")/../.." && pwd)/models/Muse-Glimmer-30B}"
export VLLM_SERVED_NAME=muse-glimmer:30b VLLM_PORT="${VLLM_PORT:-8006}" VLLM_GPU="${VLLM_GPU:-1}" \
       VLLM_GPU_UTIL="${VLLM_GPU_UTIL:-0.40}" VLLM_LOG_NAME=vllm-muse \
       VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-131072}" VLLM_MAX_NUM_SEQS="${VLLM_MAX_NUM_SEQS:-32}" \
       VLLM_REASONING_PARSER=muse_glimmer VLLM_TOOL_PARSER=muse_glimmer
exec "$(dirname "$0")/start_vllm.sh"
