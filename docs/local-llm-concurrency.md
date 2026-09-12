# 로컬 모델 다중접속 기획 — Ollama의 천장을 재고, vLLM으로 가는 길

2026-09-12. 이 노드(NVIDIA B200 183GB × 2, CUDA 13.1, Ollama 0.32.5)에서 실측한 수치와,
그 수치가 고른 설계입니다. 재현 스크립트는 `scripts/bench_local_concurrency.py`.

## 0. 결론 먼저 — 2026-09-12 vLLM으로 전환했다

같은 노드, 같은 요청 모양(입력 1,123토큰·출력 최대 256), gemma4:26b:

| 동시 | Ollama 슬롯 4 | Ollama 슬롯 16 | **vLLM 0.26 (BF16)** |
|---|---|---|---|
| 1 | 1.9s | - | **0.9s** |
| 8 | 7.5s | 6.2s | **1.1s** |
| 16 | 13.4s | 11.5s | **1.8s** |
| 32 | - | 24.1s | **1.7s** |
| 64 | - | - | **2.0s** (합산 4,884 tok/s) |

Ollama의 합산 처리량 천장이 350 tok/s였던 자리에서 vLLM은 4,884 tok/s를 냈다(14배). 64명이
동시에 물어도 한 사람이 2초 안에 답을 받는다. 도구 호출(tool_calls 정상 파싱)·사고 끄기
(reasoning_effort none → enable_thinking=false)·한국어 답변 품질을 확인했고, MOPAN API로
7턴 실대화까지 돌렸다. 아래 §1~§4는 그 판단에 이른 과정이고, **지금 상태는 §6**이다.

원래의 단계별 결론(전환 전):

| 동시 사용자 | 지금(Ollama, 슬롯 4) | 권고 |
|---|---|---|
| ~4명 | 답 하나 4초대. 그대로 쓴다 | 아무것도 바꾸지 않는다 |
| 5~12명 | 8명 7.5초, 16명 13초 | **1단계: Ollama 슬롯 16** (환경변수 한 줄, 실측 -15~20%) |
| 12명 이상 | 32명 24초, 처리량 350 tok/s에서 멈춤 | **2단계: 답변 모델을 vLLM으로** (연속 배칭, 같은 GPU에서 수 배) |

MOPAN 쪽 코드는 어느 단계든 **거의 그대로**입니다. 로컬 서버는 OpenAI 호환 `/v1` 하나로
추상화되어 있고(`LOCAL_LLM_BASE_URL`), 모델 발견은 `/v1/models`, 라우팅은 카탈로그가
합니다. Ollama 고유였던 keep_alive 고정만 vLLM에선 건너뛰게 고쳤습니다(아래 §4).

## 1. 실측 — Ollama는 B200에서 350 tok/s가 천장이다

요청 하나 = RAG 답변 프롬프트 모양(입력 1,123토큰, 근거 10개) + 출력 최대 256토큰.
동시 N개를 한꺼번에 보내 전부 끝날 때까지 잰 값. 콜드 로드는 제외(워밍업 1회).

**현재 운영 인스턴스(OLLAMA_NUM_PARALLEL=4), gemma4:26b**

| 동시 | 전부 끝 | p50 | p95 | 합산 생성 tok/s |
|---|---|---|---|---|
| 1 | 1.9s | 1.9s | 1.9s | 137 |
| 4 | 4.3s | 4.3s | 4.3s | 238 |
| 8 | 7.5s | 5.8s | 7.5s | 272 |
| 16 | 13.4s | 8.8s | 13.4s | 305 |

**같은 노드에 임시로 띄운 두 번째 인스턴스(OLLAMA_NUM_PARALLEL=16, 포트 11435), gemma4:26b**

| 동시 | 전부 끝 | p50 | p95 | 합산 생성 tok/s |
|---|---|---|---|---|
| 4 | 4.3s | 4.3s | 4.3s | 239 |
| 8 | 6.2s | 6.2s | 6.2s | 331 |
| 16 | 11.5s | 11.4s | 11.5s | 357 |
| 32 | 24.1s | 20.2s | 24.1s | 340 |

읽는 법:

- 슬롯을 4→16으로 늘리면 8명에서 17%, 16명에서 14% 빨라진다. 공짜지만 그게 전부다.
- **합산 처리량이 350 tok/s 근처에서 멈춘다.** 슬롯이 남아도 늘지 않는다. Ollama(llama.cpp
  런타임)는 슬롯별로 순차에 가깝게 디코딩하고 vLLM식 연속 배칭이 없다 - B200 한 장의
  연산 여력에 비해 한 자릿수 %만 쓰고 있다는 뜻이다. 32명이면 한 사람당 20초 대기.
- 참고로 gemma4:26b는 MoE(활성 4B)라 단일 요청은 137 tok/s로 빠르다. 문제는 단일이 아니라
  합산이다.
- 지연은 사용자 수에 거의 선형이다(8→16→32명: 6→11→24초). 큐가 서버 안에서 서는 것이라
  오류(errors=0)는 없고, 그냥 기다린다. MOPAN의 로컬 클라이언트 타임아웃은 120초라
  50명 남짓까지는 오류 없이 "느림"으로만 나타난다.

## 2. 후보 비교

| 엔진 | 다중접속 방식 | 장점 | 단점 | 판정 |
|---|---|---|---|---|
| **Ollama**(현행) | 고정 슬롯(NUM_PARALLEL) + 큐 | 모델 태그 한 줄로 받음, 여러 모델 동시 상주, 5분 언로드(우린 keep_alive=-1로 끔) | 연속 배칭 없음 → 실측 350 tok/s 천장, 슬롯마다 KV 캐시 전체 복제 | **~8명까지** |
| **vLLM** | 연속 배칭 + PagedAttention | 같은 GPU에서 수 배~수십 배 합산 처리량, 공통 접두사(시스템 프롬프트) KV 재사용, 텐서 병렬, AWQ/FP8 | 프로세스당 모델 하나, HF 저장소 id로 받음(Gemma 4는 게이트 - 토큰 필요), 기동 1~2분 | **12명 이상이면 이것** |
| llama.cpp server | `--parallel N -cb`(연속 배칭 기본) | Ollama보다 가볍고 GGUF 그대로 씀 | 처리량은 vLLM에 못 미침, 운영 도구 빈약 | Ollama 대체로는 가능, 굳이 |
| SGLang | RadixAttention(접두사 캐시) | RAG·멀티턴처럼 접두사가 겹치는 부하에서 vLLM보다 빠른 보고 다수 | 생태계·문서가 vLLM보다 얇음 | vLLM 다음 후보 |
| TGI / LMDeploy | 연속 배칭 | 성숙 / 빠름 | TGI는 유지보수 모드, LMDeploy는 백엔드 종속 | 보류 |

근거(2026 자료):

- vLLM은 v0.19.0(2026-04-02)에서 Gemma 4 전 라인업(E2B·E4B·26B-A4B·31B)을 Day-0 지원 -
  https://github.com/vllm-project/vllm-project.github.io/blob/main/_posts/2026-04-02-gemma4.md
- Ollama vs vLLM 동시 부하 벤치(A100, GuideLLM): 50명 동시에서 p95 18.4s vs 2.1s, 256명 동시에서
  41 vs 793 tok/s - https://www.sitepoint.com/ollama-vs-vllm-performance-benchmark-2026/
- 8명 동시에서 vLLM이 2.3배 - https://robert-mcdermott.medium.com/performance-vs-practicality-a-comparison-of-vllm-and-ollama-104acad250fd
- Ollama 슬롯·큐 동작(NUM_PARALLEL·MAX_QUEUE 512 초과 시 503) - https://www.ssdnodes.com/learn/ollama-num-parallel-and-max-queue
- vLLM 접두사 캐시 - https://bentoml.com/llm/inference-optimization/prefix-caching
- vLLM 임베딩 서빙(`--runner pooling`, 별도 프로세스) - https://docs.vllm.ai/en/latest/models/pooling_models/embed/
- vLLM 프로세스당 모델 하나 - https://discuss.vllm.ai/t/run-multiple-models/1181/4
- 대안 비교 - https://www.premai.io/blog/llm-inference-servers-compared-vllm-vs-tgi-vs-sglang-vs-triton-2026/

## 3. 1단계 — Ollama 슬롯 16 (지금, 재배포 없음)

Ollama는 새싹이 쪽 `saessagi-Linux/start.sh`가 `OLLAMA_NUM_PARALLEL=4`로 띄운다. 거기 값을
16으로 올리고 Ollama만 재시작한다. 재시작 뒤 MOPAN 백엔드도 재시작해야 keep_alive=-1
고정(기동 시 한 번)이 다시 걸린다. 메모리: 슬롯마다 KV 캐시가 복제되므로 26b가 지금
19.5GiB → 대략 40GiB대. GPU 두 장에 각 80GB 남아 있어 문제없다.

```
# saessagi-Linux/start.sh 의 OLLAMA_NUM_PARALLEL 기본값 4 → 16
OLLAMA_NUM_PARALLEL=16 OLLAMA_MAX_QUEUE=256 OLLAMA_FLASH_ATTENTION=1 ollama serve
```

- `OLLAMA_MAX_QUEUE=256`: 큐가 이보다 길면 503으로 바로 거절한다. 120초 타임아웃에 걸려
  조용히 실패하는 것보다 "지금 붐빕니다"가 낫다.
- `OLLAMA_FLASH_ATTENTION=1`: 슬롯이 늘 때 KV 메모리와 프리필 시간을 줄인다. 실측은
  안 했다 - 켜고 같은 벤치를 돌려 비교할 것.
- 기대치: 8명 6초대, 16명 11초대(위 표). 그 이상은 이 단계로 안 된다.

## 4. 2단계 — 답변 모델을 vLLM으로

### 4.1 배치 (이 노드 기준)

| 프로세스 | 모델 | GPU | 포트 | 메모 |
|---|---|---|---|---|
| vLLM chat | `google/gemma-4-26b-it` (또는 31b) | 0 | 8001 | BF16 ~55GB + KV. `--max-num-seqs 64 --max-model-len 16384` |
| vLLM cheap | `google/gemma-4-e4b-it` | 1 | 8002 | 의도 분류·질의 압축·대화 요약. 작은 모델은 별도 프로세스가 맞다 |
| Ollama(유지) | `qwen3-embedding:8b` | 1 | 11434 | 임베딩은 프리필뿐이라 가볍다. 나중에 `vllm serve Qwen/Qwen3-Embedding-8B --runner pooling`(포트 8003)으로 옮길 수 있다 |

MOPAN은 로컬 base URL이 **하나**다(`LOCAL_LLM_BASE_URL`). 프로세스가 여럿이면 앞에 nginx
한 장을 두고 모델 이름으로 나눈다(`/v1/models`는 셋을 합쳐 답하게). 첫 도입은 chat 하나만
vLLM으로 옮기고 e4b·임베딩은 Ollama에 두는 것이 가장 짧다 - 그러면 nginx가 필요하다.
가장 짧은 대안: Ollama의 `/v1`을 그대로 base로 두고, 카탈로그의 provider가 "local-vllm"
같은 두 번째 base를 갖게 코드를 한 줄 늘리는 것. 어느 쪽이든 하루 일이다.

### 4.2 설치와 기동

```
# 별도 venv (백엔드 .venv와 섞지 않는다 - torch가 다르다)
uv venv /NHNHOME/WORKSPACE/26rda001_A/SAS/soonkun/opt/vllm && source .../opt/vllm/bin/activate
uv pip install vllm            # ≥ 0.19 (Gemma 4), CUDA 13 휠
huggingface-cli login          # Gemma 4는 게이트 모델: HF에서 라이선스 동의 후 토큰 필요 (실측: 토큰 없이 401)

CUDA_VISIBLE_DEVICES=0 vllm serve google/gemma-4-26b-it \
  --port 8001 --max-num-seqs 64 --max-model-len 16384 \
  --gpu-memory-utilization 0.85 --enable-prefix-caching \
  --served-model-name gemma4:26b
```

- `--served-model-name gemma4:26b`: MOPAN 카탈로그(`llm_models`)·사용자 선택·추적 기록이
  전부 `gemma4:26b`라는 이름을 쓴다. 이름을 그대로 서빙하면 DB·화면·설정을 안 건드린다.
  (`OpenAIProvider._is_local`이 `:`가 든 이름을 로컬로 보는 관례도 그대로 살아남는다.)
- `--enable-prefix-caching`: MOPAN의 시스템 프롬프트(answer_agent, 수천 자)는 모든 요청의
  접두사다. 근거·이력은 그 뒤에 온다(build_prompt 순서: 시스템 → 이력 → 근거 → 질문). 공통
  접두사가 앞에 있어 캐시가 맞는다 - 멀티턴 요약도 시스템 메시지 끝에 붙으므로 대화가
  같으면 그것까지 재사용된다.
- `--max-model-len 16384`: MOPAN 프롬프트 상한은 예산 8,000 + 필수 2,000 + 출력. 26b의
  262k 컨텍스트를 다 열면 KV 메모리만 먹고 동시 수가 준다.
- 양자화: B200엔 BF16이 그냥 들어간다. 메모리를 아껴야 할 때만 Google의 QAT W4A16
  체크포인트(31b가 19.8GB).

### 4.3 MOPAN 쪽 변경 (이미 한 것 / 남은 것)

- (완료) `pin_local_models`: `/api/version`이 없으면 Ollama가 아니므로 keep_alive 고정을
  건너뛴다. vLLM은 프로세스와 함께 상주한다.
- (완료) 모델 발견 `/v1/models`·채팅 `/v1/chat/completions`·임베딩 `/v1/embeddings`는 이미
  OpenAI 호환 그대로다.
- (완료) 로컬 base가 둘일 때의 라우팅: `VLLM_BASE_URL`(두 번째 로컬 서버). 기동 시 그쪽
  `/v1/models`가 내는 이름을 프로바이더에 심고(`vllm_model_names`), 그 이름만 vLLM으로,
  나머지 로컬 이름은 Ollama로 보낸다. vLLM이 내는 이름은 Ollama에 상주시키지 않는다.
  nginx 없이 코드 한 줄짜리 대안(4.1)이 이것이다.
- (남음) 스트리밍. 지금 답은 `done` 프레임 하나로 도착한다. 동시 사용자가 늘면 첫 토큰까지
  기다리는 시간이 곧 체감이라, vLLM으로 가면 `stream=True`로 토큰을 흘리는 것이 다음
  체감 개선이다. 백엔드 SSE 골격은 이미 있어 프레임 하나를 더 정의하면 된다.
- (보류) 앱 쪽 세마포어/큐. 서버(Ollama MAX_QUEUE, vLLM 내부 큐)가 이미 줄을 세운다.
  p95 > 30초가 관측되면 그때 `asyncio.Semaphore(max_num_seqs)` + 즉시 503을 넣는다.

### 4.4 검증 계획

1. vLLM chat을 8001에 띄우고 `scripts/bench_local_concurrency.py http://127.0.0.1:8001/v1 gemma4:26b 1,4,8,16,32`
   - 기대: 16명에서 p95 3~4초 이내, 합산 1,500 tok/s 이상(문헌 기준 Ollama의 4~10배).
2. 같은 벤치를 32·64로 늘려 `--max-num-seqs`와 KV 메모리(`nvidia-smi`)의 관계를 적는다.
3. 평가 하네스(`scripts/eval_retrieval.py`)로 답변 품질이 Ollama GGUF Q4_K_M ↔ vLLM BF16에서
   같은지 본다 - 양자화가 다르므로 미세하게 다를 수 있다(보통 BF16 쪽이 낫다).
4. `LOCAL_LLM_BASE_URL`을 바꿔 MOPAN을 붙이고 스모크(`scripts/smoke_test.py`).

## 5. 위험과 되돌리기

- Gemma 4는 HF 게이트 모델이다. 토큰 없이는 못 받는다(실측 401). 라이선스 동의는 사람이 한다.
- vLLM 기동은 1~2분(가중치 로드 + CUDA 그래프 캡처). 재시작이 잦은 운영이면 체감된다.
  Ollama처럼 모델을 바꿔 가며 쓰는 유연성은 없다 - 모델 하나에 프로세스 하나.
- 되돌리기는 `LOCAL_LLM_BASE_URL`을 Ollama로 되돌리는 것뿐이다. DB·프롬프트·설정은 안 바뀐다.
- GPU는 새싹이(50002 포트, 같은 Ollama)와 공유한다. vLLM에 `--gpu-memory-utilization 0.85`를
  주면 그 GPU의 85%를 선점하므로, Ollama가 같은 GPU에 올린 모델과 합이 넘지 않게 GPU를
  나눈다(4.1의 배치가 그것이다).

## 6. 지금 상태 (2026-09-12 전환 후)

| 프로세스 | 모델 | GPU | 포트 | 기동 |
|---|---|---|---|---|
| vLLM 0.26 | `gemma4:26b` = models/gemma-4-26B-A4B-it (BF16, 48.5GiB) | 1 (42% ≈ 77GB 선점) | 8001 | `scripts/start_vllm.sh` (start_local.sh가 부른다) |
| Ollama 0.32.5 | `gemma4:e4b`(값싼 단계), `gemma4:31b`, `qwen3-embedding:8b` | 0·1 | 11434 | 새싹이 start.sh |

- `.env`: `VLLM_BASE_URL=http://127.0.0.1:8001/v1`. 지우면(또는 vLLM이 죽어 있으면) 발견이
  빈 집합이라 `gemma4:26b`는 Ollama로 돌아간다 - 되돌리기는 그것뿐이다. 단, MOPAN 기동 시
  Ollama의 26b는 이제 고정하지 않으므로 그 경우 첫 요청이 콜드 로드(수 초)를 문다.
- vLLM 기동은 실측 약 4분 20초(가중치 로드 20초 + 프로파일링·CUDA 그래프 캡처). 그 사이
  MOPAN이 먼저 떴으면 관리자 화면 "모델 → 새로고침"(`POST /api/models/refresh`)이 다시
  발견한다.
- 사고(thinking): MOPAN은 추론 모델로 표시되지 않은 로컬 모델에 항상 `reasoning_effort:
  none`을 보내고, vLLM은 그것을 `enable_thinking=false`로 옮긴다. 사용자가 26b를 추론
  모델로 표시하고 수준을 고르면 `--reasoning-parser gemma4`가 사고 본문을 분리한다.
- 남은 것: (1) 답변 스트리밍(첫 토큰 체감), (2) 임베딩을 vLLM pooling 인스턴스로(지금은
  Ollama, 부하가 가벼워 급하지 않다), (3) e4b도 vLLM으로(값싼 단계 셋이 매 질문마다 도는데
  Ollama 슬롯을 쓴다 - 동시 사용자가 더 늘면 다음 병목은 여기다).
- 재현: `python scripts/bench_local_concurrency.py http://127.0.0.1:8001/v1 gemma4:26b 1,4,8,16,32,64`
