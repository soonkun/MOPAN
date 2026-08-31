"""Live /api/chat probe, through the same Next.js proxy a browser uses.

    python scripts/check_chat.py "상표 지정상품은 어떻게 정하나요?"

Prints the answer, then the numbers the owner reads off the trace panel:
how many evidence items were DELIVERED to the model, how many the answer
actually CITED, and which prompt answered - "clarify_agent" means the
weak-evidence branch fired and the reply is a question back rather than an
answer.

Here because /api/search cannot show any of that: it is retrieval only and never
reaches the answer path, so the 14-delivered/0-cited failure is invisible to it.
"""

import json
import sys

import httpx

BASE = "http://localhost:3000"
DEFAULT = "상표 지정상품은 어떻게 정하나요?"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    with httpx.Client(base_url=BASE, timeout=180, follow_redirects=True) as client:
        response = client.post(
            "/api/auth/login",
            json={"email": "smoke-admin@example.com", "password": "smoke-admin-pw-123"},
        )
        if response.status_code != 200:
            print(f"login failed: {response.status_code} {response.text[:200]}")
            return 1

        answer, message_id = "", None
        with client.stream("POST", "/api/chat", json={"message": question}) as stream:
            if stream.status_code != 200:
                stream.read()
                print(f"chat failed: {stream.status_code} {stream.text[:300]}")
                return 1
            for line in stream.iter_lines():
                if not line.startswith("data:"):
                    continue
                frame = json.loads(line[5:])
                if frame.get("type") == "token":
                    answer += frame.get("text", "")
                elif frame.get("type") == "done":
                    # The answer arrives whole on `done` in this build, not as
                    # token frames; take whichever is populated.
                    answer = answer or frame.get("content", "")
                    message_id = frame.get("message_id")
                elif frame.get("type") == "error":
                    print(f"stream error: {frame}")

        print(f"Q: {question}\n")
        print(f"A: {answer.strip()[:1200]}\n")

        if not message_id:
            print("(no message_id in the stream - cannot read the trace)")
            return 0
        trace = client.get(f"/api/messages/{message_id}/trace")
        if trace.status_code != 200:
            print(f"trace unavailable: {trace.status_code} {trace.text[:200]}")
            return 0
        body = trace.json()
        retrieval = body.get("retrieval") or {}
        util = body.get("utilization") or {}
        print(
            f"prompt      : {body.get('prompt_name')}\n"
            f"retrieved   : {retrieval.get('evidence_count')}\n"
            f"delivered   : {util.get('delivered')}\n"
            f"cited       : {util.get('cited')}\n"
            f"utilization : {util.get('utilization')}\n"
            f"nothing_cited: {util.get('nothing_cited')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
