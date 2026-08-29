"""End-to-end smoke test against a running stack.

    python scripts/smoke_test.py [base_url]     # default http://localhost:3000

Runs against the FRONTEND origin by default, which is what a real browser talks
to - so it also proves the /api/* rewrite proxy works. Pure Python + httpx: no
bash, no curl, no /tmp literals, identical on Windows and Linux.
"""
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

INGEST_TIMEOUT_SECONDS = 120

SAMPLE = """# 스모크 테스트 문서

토마토 역병은 감염된 토양을 통해 퍼진다.
"""


def main(base_url: str) -> int:
    # A Windows console is often cp949 and every printed chunk here is Korean.
    # Without this a UnicodeEncodeError in a print would surface as a smoke-test
    # failure that has nothing to do with the stack.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # A fresh account by default, so the script needs no setup and leaves no
    # shared state behind. But the FIRST account on a deployment is the admin and
    # every later one is not, so on a stack that already has users the fresh
    # account cannot upload - and the ingestion path is the half of this script
    # worth running. Point it at an existing admin to exercise all of it.
    email = os.environ.get("MOPAN_SMOKE_EMAIL") or f"smoke-{uuid.uuid4().hex[:8]}@example.com"
    password = os.environ.get("MOPAN_SMOKE_PASSWORD", "smoke-pw-123")
    tmp = Path(tempfile.gettempdir()) / f"mopan-smoke-{uuid.uuid4().hex[:8]}.md"
    tmp.write_text(SAMPLE, encoding="utf-8")

    with httpx.Client(base_url=base_url, timeout=30.0, follow_redirects=True) as client:
        print("1/6 health...")
        response = client.get("/api/health")
        response.raise_for_status()
        assert response.json()["status"] == "ok"

        print("2/6 readiness...")
        client.get("/api/health/ready").raise_for_status()

        print("3/6 register + login...")
        register = client.post(
            "/api/auth/register", json={"email": email, "password": password}
        )
        if register.status_code not in (200, 400):
            register.raise_for_status()
        client.post("/api/auth/login", json={"email": email, "password": password}).raise_for_status()

        me = client.get("/api/auth/me")
        me.raise_for_status()
        role = me.json()["role"]
        print(f"    logged in as {email} ({role})")

        print("4/6 collections...")
        collections = client.get("/api/collections")
        collections.raise_for_status()
        if collections.json():
            collection_id = collections.json()[0]["id"]
        elif role == "admin":
            created = client.post("/api/collections", json={"name": "Smoke Test"})
            created.raise_for_status()
            collection_id = created.json()["id"]
        else:
            print("    no collection available and this account is not admin; skipping upload")
            return 0

        document_id = None
        if role == "admin":
            print("5/6 upload...")
            with tmp.open("rb") as handle:
                upload = client.post(
                    "/api/documents",
                    data={"collection_id": collection_id},
                    files={"file": (tmp.name, handle, "text/markdown")},
                )
            upload.raise_for_status()
            document_id = upload.json()["id"]
            print(f"    uploaded {document_id} (status={upload.json()['status']})")

            # Upload returns 202 the moment the file is on disk; parse, chunk,
            # embed and index all happen in the arq worker afterwards. Searching
            # straight away therefore queries an index this document is not in
            # yet, and the whole point of this script is to prove that path ran.
            print("    waiting for the pipeline...")
            for _ in range(INGEST_TIMEOUT_SECONDS * 2):
                document = client.get(f"/api/documents/{document_id}")
                document.raise_for_status()
                status = document.json()["status"]
                if status == "indexed":
                    print(f"    indexed, {document.json()['chunk_count']} chunk(s)")
                    break
                if status == "failed":
                    print(f"    FAILED: {document.json()['error_message']}")
                    return 1
                time.sleep(0.5)
            else:
                print(f"    still {status} after {INGEST_TIMEOUT_SECONDS}s - is the worker running?")
                return 1
        else:
            print("5/6 upload skipped (not admin)")

        print("6/6 search...")
        search = client.post("/api/search", json={"query": "역병"})
        search.raise_for_status()
        results = search.json()["results"]
        print(f"    {len(results)} result(s)")
        # Assert, do not just print. A retrieval path that silently returns
        # nothing is the failure this script exists to catch, and printing 0 and
        # exiting 0 would report it as a pass.
        if document_id is not None:
            # document_id lives under metadata, not at the top level: a result is
            # an Evidence, and Evidence is the abstraction that lets Slice 3 mix
            # MCP results into this same list, where a document id would mean
            # nothing. source_type/ref are the fields every source shares.
            hit = next(
                (r for r in results if r["metadata"].get("document_id") == document_id), None
            )
            assert hit is not None, (
                f"the document just indexed is not in the results for 역병: "
                f"{[r['ref'] for r in results]}"
            )
            assert "역병" in hit["content"], f"matched chunk does not contain the term: {hit['ref']}"
            print(f"    found it at rank {results.index(hit) + 1} of {len(results)}")

    tmp.unlink(missing_ok=True)
    print("All smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:3000"))
