#!/usr/bin/env bash
# 코딩 에이전트용 Qwen3.8-27B-FP8(Apache 2.0, 31GB)를 GPU0 8005에 띄운다. 선정 근거·벤치마크:
# docs/superpowers/plans/2026-09-25-code-model.md. 파서는 vLLM 레시피(reasoning qwen3, tool qwen3_coder).
# 45%(≈82GB) = 가중치 31GB + 128k 창 KV. 같은 GPU0의 e4b(12%)·임베딩(25%)과 합쳐 82%.
set -euo pipefail
export VLLM_MODEL_PATH="${VLLM_MODEL_PATH:-$(cd "$(dirname "$0")/../.." && pwd)/models/Qwen3.8-27B-FP8}"
export VLLM_SERVED_NAME=qwen3.8:27b VLLM_PORT="${VLLM_PORT:-8005}" VLLM_GPU="${VLLM_GPU:-0}" \
       VLLM_GPU_UTIL="${VLLM_GPU_UTIL:-0.45}" VLLM_LOG_NAME=vllm-qwen \
       VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-131072}" VLLM_MAX_NUM_SEQS="${VLLM_MAX_NUM_SEQS:-32}" \
       VLLM_REASONING_PARSER=qwen3 VLLM_TOOL_PARSER=qwen3_coder
exec "$(dirname "$0")/start_vllm.sh"
