"""Live /api/search probe, through the same Next.js proxy a browser uses.

    python scripts/check_search.py
    python scripts/check_search.py "조약우선권 증명서류는 언제까지 내야 하나요?"

Here rather than in the test suite because it is the only check that exercises
the DEPLOYED container rather than the working tree - twice on this project an
image was rebuilt without the running container being replaced, and a passing
test suite said nothing about it. Reads no secrets: the smoke admin's
credentials are the ones already in scripts/smoke_test.py's flow.
"""

import json
import sys

import httpx

BASE = "http://localhost:3000"
DEFAULT = "조약우선권주장의 기초가 되는 제1국출원으로 상표등록출원도 인정되나요?"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    query = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    with httpx.Client(base_url=BASE, timeout=120, follow_redirects=True) as client:
        response = client.post(
            "/api/auth/login",
            json={"email": "smoke-admin@example.com", "password": "smoke-admin-pw-123"},
        )
        if response.status_code != 200:
            print(f"login failed: {response.status_code} {response.text[:200]}")
            return 1
        response = client.post("/api/search", json={"query": query})
        if response.status_code != 200:
            print(f"search failed: {response.status_code} {response.text[:300]}")
            return 1
        data = response.json()

    print(f"query: {data['query']}\n")
    header = f"{'#':>3}  {'page':>5} {'idx':>5} {'dense':>6} {'sparse':>6} {'rrf':>9} {'rerank':>8}"
    print(header)
    print("-" * len(header))
    hit = None
    for rank, item in enumerate(data["results"], 1):
        meta = item["metadata"]
        rerank = meta.get("rerank_score")
        print(
            f"{rank:>3}  {str(meta.get('page')):>5} {str(meta.get('chunk_index')):>5} "
            f"{str(meta.get('vector_rank')):>6} {str(meta.get('keyword_rank')):>6} "
            f"{meta.get('rrf_score', 0):>9.6f} "
            f"{'-' if rerank is None else format(rerank, '.4f'):>8}"
        )
        if meta.get("page") == 573 and hit is None:
            hit = (rank, item)
    if hit:
        rank, item = hit
        print(f"\np573 at rank {rank}:")
        print("   ", json.dumps(item["content"][:200], ensure_ascii=False))
    else:
        print("\np573 NOT in the returned set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
