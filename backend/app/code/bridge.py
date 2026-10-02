"""내 컴퓨터 연결 - 사용자 PC의 컴패니언(mopan-code.mjs)이 아웃바운드로만 붙는 역방향 터널.

WebSocket이 아니다. 공개 주소는 cloudflared → Next.js(3000) → 백엔드인데 Next의 rewrites 프록시가
WebSocket 업그레이드를 넘기지 않는다. 대신 이미 검증된 두 길만 쓴다:
- 내려가는 길: 컴패니언이 `GET /api/code/bridge/stream`을 SSE로 열어 두고, 서버는 "이 HTTP 요청을
  네 PC의 opencode에 대신 보내라"는 프레임을 그 스트림으로 흘린다.
- 올라오는 길: 컴패니언이 응답을 `POST /api/code/bridge/reply/{id}` 프레임으로 돌려준다. 첫 프레임에
  status·headers, 이어서 chunk(b64), 마지막에 end. opencode의 /event(SSE)처럼 끝없는 응답은 chunk가
  계속 이어진다.

보안 모델: 이 연결은 그 사용자가 자기 PC에서 자기 에이전트를 돌리는 것이고, 조종은 MOPAN에 로그인한
같은 사람만 한다(프록시 라우트가 세션 쿠키로 사용자를 확인해 그 사용자의 연결만 고른다). 토큰은
컴패니언 하나를 그 계정에 묶는 열쇠라 해시로만 저장하고 언제든 새로 발급해 이전 것을 죽인다.
"""

from __future__ import annotations

import asyncio
import base64
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

FIRST_FRAME_TIMEOUT = 60.0
# opencode /event는 heartbeat를 흘리므로 살아 있는 스트림은 이 안에 무언가 온다.
IDLE_FRAME_TIMEOUT = 300.0


class BridgeError(Exception):
    pass


@dataclass
class BridgeConn:
    user_id: uuid.UUID
    dirs: list[str]
    host: str
    version: str
    connected_at: float = field(default_factory=time.time)
    # 상한: 컴패니언이 응답 프레임을 쏟아붓거나 브라우저가 요청을 쏟아부어도 메모리가 무한정 자라지 않게.
    outgoing: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=2000))
    pending: dict[str, asyncio.Queue] = field(default_factory=dict)
    alive: bool = True


class Bridge:
    def __init__(self) -> None:
        self.conns: dict[uuid.UUID, BridgeConn] = {}

    def connect(self, user_id: uuid.UUID, dirs: list[str], host: str, version: str) -> BridgeConn:
        old = self.conns.get(user_id)
        if old is not None:
            self.disconnect(old)
        conn = BridgeConn(user_id=user_id, dirs=dirs, host=host, version=version)
        self.conns[user_id] = conn
        return conn

    def disconnect(self, conn: BridgeConn) -> None:
        conn.alive = False
        if self.conns.get(conn.user_id) is conn:
            del self.conns[conn.user_id]
        conn.outgoing.put_nowait(None)
        for q in list(conn.pending.values()):
            q.put_nowait({"error": "내 컴퓨터 연결이 끊어졌습니다."})

    def get(self, user_id: uuid.UUID) -> BridgeConn | None:
        conn = self.conns.get(user_id)
        return conn if conn is not None and conn.alive else None

    def reply(self, conn: BridgeConn, request_id: str, frame: dict) -> bool:
        q = conn.pending.get(request_id)
        if q is None:
            return False
        try:
            q.put_nowait(frame)
        except asyncio.QueueFull:
            # 브라우저가 소비하지 않는 스트림 - 더 쌓지 않고 끊는다.
            conn.pending.pop(request_id, None)
            return False
        return True

    async def request(
        self,
        conn: BridgeConn,
        method: str,
        path: str,
        query: dict[str, str],
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int, dict[str, str], AsyncIterator[bytes]]:
        request_id = uuid.uuid4().hex
        q: asyncio.Queue = asyncio.Queue(maxsize=5000)
        conn.pending[request_id] = q
        await conn.outgoing.put(
            {
                "id": request_id,
                "method": method,
                "path": path,
                "query": query,
                "headers": headers,
                "body": base64.b64encode(body).decode() if body else None,
            }
        )
        try:
            first = await asyncio.wait_for(q.get(), FIRST_FRAME_TIMEOUT)
        except TimeoutError:
            conn.pending.pop(request_id, None)
            conn.outgoing.put_nowait({"id": request_id, "cancel": True})
            raise BridgeError("내 컴퓨터의 컴패니언이 응답하지 않습니다.") from None
        if "error" in first:
            conn.pending.pop(request_id, None)
            raise BridgeError(first["error"])

        async def body_iter() -> AsyncIterator[bytes]:
            frame = first
            done = False
            try:
                while True:
                    if frame.get("chunk"):
                        yield base64.b64decode(frame["chunk"])
                    if frame.get("end") or "error" in frame:
                        done = True
                        break
                    try:
                        frame = await asyncio.wait_for(q.get(), IDLE_FRAME_TIMEOUT)
                    except TimeoutError:
                        break
            finally:
                conn.pending.pop(request_id, None)
                if not done and conn.alive:
                    # 브라우저가 먼저 떠났다(EventSource 닫힘) - PC 쪽 fetch도 끊게 한다.
                    conn.outgoing.put_nowait({"id": request_id, "cancel": True})

        return int(first.get("status", 502)), dict(first.get("headers") or {}), body_iter()
