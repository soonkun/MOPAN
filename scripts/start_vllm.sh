#!/usr/bin/env bash
# vLLM으로 로컬 모델을 서빙한다. 기본값은 답변 모델(gemma4:26b = google/gemma-4-26B-A4B-it BF16)이고, 다른 모델은
# 환경변수만 바꿔 같은 스크립트로 띄운다: start_vllm_e4b.sh(8002)·start_vllm_embed.sh(8003, 임베딩)·
# start_vllm_qwen.sh(8005)·start_vllm_muse.sh(8006). 근거와 수치: docs/local-llm-concurrency.md.
#   GPU1 한 장, 총 183GB의 50%(≈92GB): 가중치 ≈48GB + KV 캐시 ≈38GB(약 18만 토큰). 같은 GPU의 다른 서버(Muse 40%)와
#   합이 100%를 넘지 않게 nvidia-smi로 free를 보고 정한다.
#   --served-model-name gemma4:26b : MOPAN 카탈로그·화면·추적 기록이 쓰는 이름 그대로.
#   --reasoning-parser/--tool-call-parser gemma4 : 사고 토큰과 함수 호출을 본문에서 분리.
#   --enable-prefix-caching : 시스템 프롬프트(모든 요청의 접두사) KV 재사용.
#   --max-model-len 131072 : 2026-10-03부터. MOPAN 프롬프트는 16k면 충분하지만, 새싹이(Ollama API → vllm-gateway → 여기)는
#     대화 문맥을 131k까지 쌓는다(agent.max_context_tokens). 요청 하나의 상한일 뿐이고 KV 캐시는 서버 전체가 나눠 쓴다.
# --enable-sleep-mode + VLLM_SERVER_DEV_MODE=1: 유휴 시 백엔드가 /sleep(level 1, 가중치를 CPU RAM으로)으로
# GPU 메모리를 비우고 첫 요청에 /wake_up으로 되살린다(app/llm/sleep.py). 없으면 두 엔드포인트가 없다.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"   # …/soonkun
# 같은 스크립트를 env로 바꿔 다른 모델도 띄운다(scripts/start_vllm_e4b.sh 참고).
MODEL="${VLLM_MODEL_PATH:-$ROOT/models/gemma-4-26B-A4B-it}"
NAME="${VLLM_SERVED_NAME:-gemma4:26b}"
PORT="${VLLM_PORT:-8001}"
GPU="${VLLM_GPU:-1}"
UTIL="${VLLM_GPU_UTIL:-0.50}"
LOG="$ROOT/MOPAN/logs/${VLLM_LOG_NAME:-vllm}.log"
# 모델별로 다른 것만 환경변수로 - 기본값은 gemma4. start_vllm_qwen.sh가 qwen3 파서·긴 창을 넘긴다.
MAXLEN="${VLLM_MAX_MODEL_LEN:-131072}"
RPARSER="${VLLM_REASONING_PARSER:-gemma4}"
TPARSER="${VLLM_TOOL_PARSER:-gemma4}"
MAXSEQS="${VLLM_MAX_NUM_SEQS:-64}"
# vLLM 실행 파일. 2026-10-03 0.26 → 0.30(opt/vllm-0.30)으로 옮겼다 - Muse Glimmer가 0.28 이상을 요구한다.
VLLM_BIN="${VLLM_BIN:-$ROOT/opt/vllm-0.30/.venv/bin/vllm}"
# VLLM_RUNNER=pooling이면 임베딩 서버로 띄운다(start_vllm_embed.sh) - 대화용 옵션(파서·도구·이미지·접두사 캐시)을 붙이지 않는다.
RUNNER="${VLLM_RUNNER:-generate}"
mkdir -p "$(dirname "$LOG")"

if curl -s -m 3 -o /dev/null "http://127.0.0.1:$PORT/v1/models"; then
  echo "vllm ($NAME) already listening on $PORT"; exit 0
fi
ARGS=("$MODEL" --served-model-name "$NAME" --host 127.0.0.1 --port "$PORT" --dtype bfloat16
      --max-model-len "$MAXLEN" --max-num-seqs "$MAXSEQS" --gpu-memory-utilization "$UTIL" --enable-sleep-mode)
if [ "$RUNNER" = pooling ]; then
  ARGS+=(--runner pooling)
else
  ARGS+=(--enable-prefix-caching --reasoning-parser "$RPARSER" --enable-auto-tool-choice --tool-call-parser "$TPARSER"
         --limit-mm-per-prompt '{"image": 5}')
fi
# 같은 GPU에는 한 번에 하나씩 띄운다. vLLM 0.30은 기동 때 GPU 전체의 빈 메모리가 얼마나 줄었는지를 재서 KV 캐시 크기를
# 정하는데, 같은 GPU에 다른 서버가 동시에 올라오면 그 적재분까지 제 것으로 세어 "No available memory for the cache
# blocks"로 죽는다(실측 2026-10-03, e4b와 임베딩을 함께 띄웠을 때). GPU별 잠금을 잡고 띄운 뒤 /v1/models가 답할 때까지
# 쥐고 있는다 - 다음 서버는 그 뒤에 뜬다. 부르는 쪽은 기다리지 않는다(백그라운드). 9>&-: 잠금을 vLLM이 물려받지 않게.
(
  flock 9
  CUDA_VISIBLE_DEVICES="$GPU" HF_HUB_OFFLINE=1 VLLM_SERVER_DEV_MODE=1 VLLM_LOGGING_LEVEL=INFO \
    setsid nohup "$VLLM_BIN" serve "${ARGS[@]}" >> "$LOG" 2>&1 < /dev/null 9>&- &
  for i in $(seq 1 720); do   # 최대 1시간 - gemma4 26B의 MoE 커널은 처음 한 번 JIT 빌드에 15분쯤 걸린다(~/.cache/flashinfer에 남는다)
    sleep 5
    curl -s -m 3 -o /dev/null "http://127.0.0.1:$PORT/v1/models" && break
    [ "$i" -gt 6 ] && ! pgrep -f "vllm serve .*--port $PORT " > /dev/null && break   # 띄우다 죽었다 - 잠금을 놓는다
  done
) 9> "/tmp/vllm-gpu$GPU.lock" > /dev/null 2>&1 &
echo "vllm $NAME starting on GPU $GPU port $PORT (같은 GPU의 앞 서버가 뜬 뒤에 시작), log: $LOG"
