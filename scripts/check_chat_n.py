"""Ask the deployed stack the same question N times and print the spread.

    python scripts/check_chat_n.py 5 "질문"

WHY N RUNS. The answer path is not deterministic - the model writes a different
answer each time, and which evidence clears the relevance floor can differ with
it - so a single run says nothing about whether a question is answered. What is
reported per run is the prompt that answered (`clarify_agent` means the reply is
a question back), how many evidence items were delivered and how many the answer
actually cited.

IT DOES NOT SAY WHETHER THE CITATIONS SUPPORT THE CLAIM. Nothing here can: a run
at candidate depth 20 once passed every count-based check in this repository while
fabricating a 상품류 list. Read the sentences with scripts/check_chat.py.
"""

import json
import sys
from collections import Counter

import httpx

BASE = "http://localhost:3000"


def once(client, question):
    answer, message_id, citations = "", None, []
    with client.stream("POST", "/api/chat", json={"message": question}) as stream:
        if stream.status_code != 200:
            stream.read()
            return {"error": stream.status_code}
        for line in stream.iter_lines():
            if not line.startswith("data:"):
                continue
            frame = json.loads(line[5:])
            if frame.get("type") == "token":
                answer += frame.get("text", "")
            elif frame.get("type") == "citations":
                citations = frame.get("citations") or []
            elif frame.get("type") == "done":
                answer = answer or frame.get("content", "")
                message_id = frame.get("message_id")
                citations = citations or frame.get("citations") or []
    row = {"cited": len(citations), "answer": answer.strip()}
    if message_id:
        trace = client.get(f"/api/messages/{message_id}/trace")
        if trace.status_code == 200:
            body = trace.json()
            row["prompt"] = body.get("prompt_name")
            row["delivered"] = (body.get("utilization") or {}).get("delivered")
            row["cited"] = (body.get("utilization") or {}).get("cited")
            row["pages"] = sorted(
                {f"{c.get('filename', '')[:6]} p.{c.get('page')}" for c in citations}
            )
    return row


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    n = int(sys.argv[1])
    question = sys.argv[2]
    with httpx.Client(base_url=BASE, timeout=240, follow_redirects=True) as client:
        if (
            client.post(
                "/api/auth/login",
                json={"email": "smoke-admin@example.com", "password": "smoke-admin-pw-123"},
            ).status_code
            != 200
        ):
            print("login failed")
            return 1
        print(f"Q: {question}")
        prompts = Counter()
        for run in range(n):
            row = once(client, question)
            prompts[row.get("prompt", "?")] += 1
            print(
                f"  run {run + 1}: {row.get('prompt')}  delivered={row.get('delivered')} "
                f"cited={row.get('cited')}  {row.get('pages')}"
            )
        print(f"  spread: {dict(prompts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
