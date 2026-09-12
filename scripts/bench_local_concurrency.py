"""로컬 LLM 서버의 동시 요청 처리량 측정 - Ollama든 vLLM이든 OpenAI 호환 /v1 이면 된다.

    python scripts/bench_local_concurrency.py [base_url] [model] [levels]
    python scripts/bench_local_concurrency.py http://127.0.0.1:11434/v1 gemma4:26b 1,4,8,16

동시 N개의 같은 요청(입력 ≈ 800토큰, 출력 최대 256토큰)을 한꺼번에 보내고 전부 끝날 때까지의
벽시계, 요청별 지연의 p50/p95, 합산 생성 토큰/초를 찍는다. 순수 표준 라이브러리 + httpx.
"""
import asyncio
import statistics
import sys
import time

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:11434/v1"
MODEL = sys.argv[2] if len(sys.argv) > 2 else "gemma4:26b"
LEVELS = [int(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "1,4,8,16").split(",")]

# RAG 답변 프롬프트의 모양을 흉내 낸다: 시스템 지시 + 근거 여러 개 + 질문.
EVIDENCE = "\n\n".join(
    f"[{i}] (규정집 {i}.pdf p.{i})\n제{i}조(정의) 이 규정에서 사용하는 용어의 뜻은 다음과 같다. "
    "1. \"상표\"란 자기의 상품과 타인의 상품을 식별하기 위하여 사용하는 표장을 말한다. "
    "2. \"출원\"이란 특허청장에게 등록을 신청하는 행위를 말한다. 3. \"류\"란 니스 분류의 상품·서비스 구분을 말한다."
    for i in range(1, 11)
)
MESSAGES = [
    {"role": "system", "content": "You are a Korean legal-document assistant. Answer only from the evidence and cite [n]."},
    {"role": "user", "content": f"<<EVIDENCE>>\n{EVIDENCE}\n<<END EVIDENCE>>"},
    {"role": "user", "content": "소셜네트워크용 앱 이름을 상표로 출원하려면 몇 류에 내야 하나요? 근거를 들어 설명해 주세요."},
]


async def one(client: httpx.AsyncClient) -> tuple[float, int, int]:
    t = time.perf_counter()
    r = await client.post(
        f"{BASE}/chat/completions",
        json={"model": MODEL, "messages": MESSAGES, "max_tokens": 256, "temperature": 0.0},
        headers={"Authorization": "Bearer none"},
    )
    r.raise_for_status()
    u = r.json().get("usage", {})
    return time.perf_counter() - t, u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


async def level(n: int) -> None:
    async with httpx.AsyncClient(timeout=600.0) as client:
        wall = time.perf_counter()
        results = await asyncio.gather(*(one(client) for _ in range(n)), return_exceptions=True)
        wall = time.perf_counter() - wall
    ok = [r for r in results if not isinstance(r, Exception)]
    errors = len(results) - len(ok)
    lat = sorted(r[0] for r in ok) or [0.0]
    p95 = lat[min(len(lat) - 1, int(round(0.95 * (len(lat) - 1))))]
    out_tokens = sum(r[2] for r in ok)
    print(
        f"concurrency={n:>3}  wall={wall:6.1f}s  p50={statistics.median(lat):6.1f}s  p95={p95:6.1f}s  "
        f"gen_tok/s={out_tokens / wall if wall else 0:7.1f}  in_tok={ok[0][1] if ok else 0}  errors={errors}",
        flush=True,
    )


async def main() -> None:
    print(f"server={BASE} model={MODEL}", flush=True)
    async with httpx.AsyncClient(timeout=600.0) as client:
        await one(client)  # 워밍업(콜드 로드 제외)
    for n in LEVELS:
        await level(n)


if __name__ == "__main__":
    asyncio.run(main())
