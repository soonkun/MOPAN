# MOPAN — 코딩 에이전트 모델 선정 (2026-09-25)

> 소유자 요구: "로컬에서 돌릴 경우를 가정해 벤치마크를 보고 추천." 코드 탭(2026-09-24-code-agent.md)의
> 기본 모델 gemma4:26b가 에이전트 루프에 약하다는 실측(hello.py 142번 읽기, 16k 창 압축 루프)에서 출발.

## 결정

**Qwen3.8-27B-FP8**(Apache 2.0, 31GB)을 GPU0 8005에 추가한다. 이름 `qwen3.8:27b`, 창 128k.
gemma4:26b는 그대로 두고 카탈로그에서 나란히 비교한다(코드 탭 모델 선택). 2주 실사용 뒤 교체 여부 결정.

## 하드웨어 필터

B200 183GB × 2. 가중치 상한: 1장 ≤ ~130GB(64k KV·다중 사용자 여유), 2장 ≤ ~280GB. 크기는 HuggingFace
safetensors 합계를 직접 읽었다(2026-09-25).

| 모델 | 가중치 | 1장 | 2장 | vLLM 0.26(이 서버) |
|---|---|---|---|---|
| Qwen3.8-27B-FP8 | 31GB | ✔ | ✔ | ✔ `Qwen3_5ForConditionalGeneration` |
| Qwen3.6-35B-A3B-FP8 | 37GB | ✔ | ✔ | ✔ |
| GLM-4.7-Flash (BF16) | 62GB | ✔ | ✔ | ✔ |
| gpt-oss-120b (MXFP4) | ~63GB | ✔ | ✔ | ✔ |
| Nemotron-3-Super-120B NVFP4 | 80GB | ✔ | ✔ | ✔ |
| Step-3.7-Flash NVFP4 | 129GB | 빡빡 | ✔ | ✔ |
| Qwen3.8-Flash-Next-NVFP4 / FP8 | 133 / 186GB | 빡빡 / ✘ | ✔ | ✘ `Qwen4Exp…` — vLLM 0.30 필요 |
| DeepSeek-V4-Flash (FP8) | 160GB | ✘ | ✔ | ✔ (레시피는 DP4+EP, TP2 검증 필요) |
| Devstral-2-123B (FP8) | 256GB | ✘ | 빡빡 | ✔ |
| GLM-5.3-Flash FP8 | 328GB | ✘ | ✘ | ✘ |
| GLM-5.3 · DeepSeek-V4.1-Flash · Kimi-K2.5 · MiniMax-M3 | 464~854GB | ✘ | ✘ | — |

## 벤치마크 (조사 에이전트 보고 요약, 출처는 아래)

(S)=벤더 자체 보고, (I)=독립 측정. **SWE-bench Verified는 2026-02 오염 이유로 사실상 폐기**, Terminal-Bench는
2.0/2.1/4.0 버전 간 비교 불가, 벤더 SWE-bench Pro는 전부 Claude Code 하네스 값. 그래서 독립 지표인
Artificial Analysis Coding Index를 기준 열로 둔다(참고: Fable 5.1 81.6, Opus 5 78.0, GPT-5.6 77.4).

| 모델 | 라이선스 | AA Coding (I) | SWE-Pro (S) | Terminal-Bench (S) | LiveCodeBench v6 (S) |
|---|---|---|---|---|---|
| Qwen3.8-Flash-Next 125B-A6B | Qwen Community(내부 사용 허용) | **73.0** | 62.5 | — | 91.9 |
| DeepSeek V4-Flash 284B-A13B | MIT | 69.1 | — | TB2.0 56.9 | 91.6 |
| **Qwen3.8-27B** | Apache 2.0 | **68.1** | 61.7 | TB2.1 73.0 | 90.3 |
| Hy3 295B-A21B | Apache 2.0 | 58.8 | 57.9 | TB2.1 71.7 | — (300GB, 탈락) |
| Qwen3.6-27B | Apache 2.0 | 53.7 | 53.5 | TB2.0 59.3 | 83.9 |
| MiniMax M2.7 229B-A10B | 수정 MIT | 52.6 | 56.2 | TB2.0 57.0 | — |
| Mistral Medium 3.5 128B | 수정 MIT | 46.9 | — | — | — |
| Qwen3.6-35B-A3B | Apache 2.0 | 41.9 | 49.5 | TB2.0 51.5 | — |
| Step 3.7 Flash 198B-A11B | Apache 2.0 | 39.6 | 56.3 | TB2.1 59.5 | — |
| **gemma4:26b (현재)** | Apache 2.0 | **39.3** | — | TB2.0 42.9(?) | 77.1 |
| Nemotron 3 Super 120B-A12B | Nemotron Open | 37.7 | — | — | 78.7 |
| gpt-oss-120b | Apache 2.0 | 30.4 | — | — | — |

Gemma 4 26B-A4B는 LiveCodeBench(코드 생성)는 괜찮은데 에이전트 지표가 낮고, 커뮤니티에 "도구 호출 거부·루프"
보고가 있다 — 우리 실측과 일치. Qwen3.8-27B는 같은 독립 지표에서 29점 위.

## 왜 Qwen3.8-27B-FP8인가

1. 1장에 들어가는 후보 중 독립 지표 최고. 2장 후보(Flash-Next 73.0, V4-Flash 69.1)와 1~5점 차.
2. Apache 2.0. Flash-Next는 라이선스 조건과 vLLM 업그레이드가 따라온다.
3. B200에서 FP8이 NVFP4보다 빠르다(vLLM recipes PR #1012: NVFP4가 Marlin 커널로 떨어짐). 27B FP8 1장 동시 4에서
   432 tok/s, 스트림당 ~150 tok/s.
4. vLLM 0.26에 아키텍처·파서(`--reasoning-parser qwen3 --tool-call-parser qwen3_coder`) 모두 있다.

주의: vLLM #58147(툴콜 마크업을 *인용*하면 가짜 tool_call, v0.28에서 보고), #39056(`<think>` 안 툴콜 유실).
reasoning_effort는 medium 기본, xhigh는 매우 느리다.

## 배치

- `scripts/start_vllm_qwen.sh` → GPU0, 8005, `--gpu-memory-utilization 0.45`(가중치 31GB + 128k KV), max-model-len 131072.
  GPU0 합계: e4b 12% + 임베딩 25% + qwen 45% = 82%.
- `start_vllm.sh`는 파서·창·max-num-seqs를 환경변수로 받게 바꿨다(기본값 gemma4 그대로).
- `.env` `VLLM_BASE_URL`에 8005 추가. 카탈로그 행 `qwen3.8:27b`(provider local, enabled).
- 코드 탭은 vLLM 모델의 창을 그 서버 `/v1/models`의 `max_model_len`에서 읽는다(`app/code/llm_proxy.py:model_limit`) —
  gemma 16k·qwen 128k처럼 서버마다 달라서 하나의 `CODE_MODEL_CONTEXT`로는 안 됐다. 그 값은 이제 OpenAI 모델용.

## 다음 단계

1. 2주 실사용: 코드 탭에서 gemma4:26b와 qwen3.8:27b를 번갈아 쓰며 루프·완주율·속도 비교.
2. 만족하면 qwen을 코드 기본으로(카탈로그 기본 모델은 답변 모델과 공유라, 코드 전용 기본이 필요해지면 그때 설정 하나 추가).
3. 부족하면 vLLM 0.30 + Qwen3.8-Flash-Next-FP8 2장(TP2) 또는 DeepSeek-V4-Flash 검토.

## 출처

Qwen3.8-27B https://huggingface.co/Qwen/Qwen3.8-27B-FP8 · vLLM 레시피 https://recipes.vllm.ai/Qwen/Qwen3.8-27B ·
Flash-Next https://huggingface.co/Qwen/Qwen3.8-Flash-Next · DeepSeek V4-Flash https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash ·
GLM-5.3-Flash https://recipes.vllm.ai/zai-org/GLM-5.3-Flash · AA Coding Index https://artificialanalysis.ai/models/open-source ·
TB2.1 https://artificialanalysis.ai/evaluations/terminalbench-2-1 · SWE-Pro https://labs.scale.com/leaderboard/swe_bench_pro ·
FP8 vs NVFP4(B200) https://github.com/vllm-project/recipes/pull/1012 · vLLM 이슈 #58147 #39056 · Gemma 4 https://benchlm.ai/models/gemma-4-26b-a4b
