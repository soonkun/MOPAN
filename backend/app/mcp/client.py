import asyncio
import ipaddress
import json
import logging
import socket
from dataclasses import dataclass
from typing import NamedTuple
from urllib.parse import urlsplit

import anyio
import httpx

logger = logging.getLogger("mopan.mcp")

# The revision of the MCP wire protocol this client speaks. Sent on every
# request as MCP-Protocol-Version, which is what lets a server that has moved on
# refuse us loudly instead of half-answering.
PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "mopan", "version": "1"}

# ponytail: the whole tool result is read into memory before it is cut to this.
# The real ceiling is the request timeout, not this constant - a server that
# streams gigabytes slowly is stopped by the clock, one that sends them fast is
# not. Swap httpx's response for an aiter_bytes loop with a running byte count if
# a registered server ever turns out to be that kind of neighbour.
MAX_RESULT_CHARS = 40_000
MAX_RESULT_FILES = 20


class ToolResult(NamedTuple):
    """tools/call의 본문 텍스트와, 도구가 resource_link로 알려 준 결과 파일들.
    files 항목: uri(상대경로 또는 http(s)), name, mime_type, size_bytes."""

    text: str
    files: list[dict]
    # 출처가 따로 있는 문서들(웹 검색이 읽은 페이지): text 블록 바로 뒤에 text/html resource_link가 오면 그 둘이
    # 한 문서다 - {text, url, name}. 이런 블록은 text·files에 들어가지 않고, 문서마다 근거 하나가 된다.
    documents: list[dict]


TRUNCATION_MARK = "\n[결과가 잘렸습니다]"

# Every message here reaches a user through HTTPException(detail=...), so they
# are Korean: frontend/lib/api.ts:detailText drops a detail with no Hangul in it.
SCHEME_MESSAGE = "MCP 서버 주소는 http:// 또는 https:// 로 시작해야 합니다."
HOST_MESSAGE = "MCP 서버 주소에서 호스트 이름을 읽지 못했습니다."
DNS_MESSAGE = "MCP 서버 주소의 호스트 이름을 확인하지 못했습니다."
PRIVATE_MESSAGE = (
    "내부망·루프백 주소로는 MCP 서버를 등록할 수 없습니다. "
    "로컬 개발용으로 허용하려면 MCP_ALLOW_PRIVATE_NETWORKS를 켜 주세요."
)
UNREACHABLE_MESSAGE = "MCP 서버에 연결하지 못했습니다. 주소와 서버 상태를 확인해 주세요."
PROTOCOL_MESSAGE = "MCP 서버가 올바른 응답을 주지 않았습니다."


class MCPError(RuntimeError):
    """Every failure of this module, so callers never import httpx to handle one.

    The message is Korean and safe to show a user: nothing that reaches it has
    passed through `redact` unredacted, and no branch below puts a request header
    into it.
    """


def redact(text: str, secret: str | None) -> str:
    """Applied to EVERY string that comes back from a registered server.

    Not paranoia about our own logging - that is handled by simply never logging
    the token. This is about the server: it receives `Authorization: Bearer
    <token>` on every call and can echo it straight back inside a tool result,
    which then becomes Evidence, a citation snippet on screen and a row in
    `messages.citations`. Redacting at the boundary is what makes "the token
    never appears in a response" true no matter what the third party sends.
    """
    if not secret:
        return text
    return text.replace(secret, "[redacted]")


@dataclass(frozen=True)
class MCPTarget:
    """Everything the client needs, detached from the ORM on purpose: a tool call
    happens with no database session open (the same rule app/chat/router.py
    follows around the LLM round trip)."""

    name: str
    base_url: str
    auth_token: str | None = None


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # `is_global` alone would do it on CPython 3.13, but the three named checks
    # are what the design document promises and what a reader has to be able to
    # verify without knowing what stdlib folds into "global".
    return ip.is_global and not (ip.is_loopback or ip.is_link_local or ip.is_private)


async def check_url(url: str, *, allow_private: bool) -> None:
    """The SSRF boundary. Discovery fetches a URL an admin typed, so without this
    the backend is a proxy for anything on the internal network - starting with
    169.254.169.254, which hands out cloud instance credentials to anyone who
    asks.

    RESIDUAL RISK, stated because there is no defence for it here: this resolves
    the name and httpx resolves it again when it connects, so a DNS record that
    answers publicly once and privately the second time (rebinding) walks past
    it. Closing that means pinning the checked address into the connection,
    which httpx does not expose without a custom transport. The escape hatch is
    the same one an operator needs for local development, which is why it is a
    single explicit flag rather than a per-server allowance.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise MCPError(SCHEME_MESSAGE)
    host = parts.hostname
    if not host:
        raise MCPError(HOST_MESSAGE)
    if allow_private:
        return

    # getaddrinfo blocks; on a cold cache that is the event loop stalled for the
    # length of a DNS lookup for every registered server.
    try:
        infos = await anyio.to_thread.run_sync(lambda: socket.getaddrinfo(host, None))
    except socket.gaierror as exc:
        raise MCPError(DNS_MESSAGE) from exc

    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:  # pragma: no cover - getaddrinfo does not produce these
            raise MCPError(DNS_MESSAGE) from None
        # EVERY resolved address, not the first: a name that answers with one
        # public and one loopback address would otherwise pass here and connect
        # to whichever the OS preferred.
        if not _is_public(ip):
            raise MCPError(PRIVATE_MESSAGE)


class MCPClient:
    """Streamable HTTP only.

    One `httpx.AsyncClient` per `async with` block, because the handshake and the
    request that follows it share a session: `initialize`, then the
    `notifications/initialized` the spec requires before any other call, then the
    call itself. A server that hands out an `Mcp-Session-Id` gets it back on
    every subsequent request in the block.
    """

    def __init__(self, target: MCPTarget, *, timeout: float, allow_private_networks: bool) -> None:
        self.target = target
        self.timeout = timeout
        self.allow_private_networks = allow_private_networks
        self._session_id: str | None = None
        self._client: httpx.AsyncClient | None = None
        self._next_id = 0

    async def __aenter__(self) -> "MCPClient":
        await check_url(self.target.base_url, allow_private=self.allow_private_networks)
        headers = {
            "Content-Type": "application/json",
            # Both, in this order: the spec lets a server answer a POST with
            # either a single JSON body or an SSE stream, and _parse handles both.
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self.target.auth_token:
            headers["Authorization"] = f"Bearer {self.target.auth_token}"
        self._client = httpx.AsyncClient(timeout=self.timeout, headers=headers, follow_redirects=False)
        try:
            await self._initialize()
        except BaseException:
            await self._client.aclose()
            self._client = None
            raise
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _redact(self, text: str) -> str:
        return redact(text, self.target.auth_token)

    async def _send(self, payload: dict) -> httpx.Response:
        assert self._client is not None
        headers = {"Mcp-Session-Id": self._session_id} if self._session_id else {}
        try:
            response = await self._client.post(self.target.base_url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            # str(exc) can quote the URL but never a header, so there is nothing
            # here to redact - and it is not shown to the user either way.
            logger.warning("mcp transport error: %s", type(exc).__name__)
            raise MCPError(UNREACHABLE_MESSAGE) from exc
        if response.status_code >= 400:
            # The body, deliberately unquoted: a 401 body from a misconfigured
            # server is exactly the kind of place a token gets echoed back.
            raise MCPError(f"{UNREACHABLE_MESSAGE} (HTTP {response.status_code})")
        return response

    def _parse(self, response: httpx.Response, request_id: int) -> dict:
        """A JSON-RPC response out of either transport shape."""
        body = self._redact(response.text)
        content_type = response.headers.get("content-type", "")
        messages: list[dict] = []
        if "text/event-stream" in content_type:
            for line in body.splitlines():
                if not line.startswith("data:"):
                    continue
                try:
                    messages.append(json.loads(line[len("data:") :].strip()))
                except json.JSONDecodeError:
                    continue
        else:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError as exc:
                raise MCPError(PROTOCOL_MESSAGE) from exc
            messages = parsed if isinstance(parsed, list) else [parsed]

        for message in messages:
            if isinstance(message, dict) and message.get("id") == request_id:
                if "error" in message:
                    detail = str((message["error"] or {}).get("message", ""))[:200]
                    raise MCPError(f"{PROTOCOL_MESSAGE} ({self._redact(detail)})")
                return message.get("result") or {}
        raise MCPError(PROTOCOL_MESSAGE)

    async def _call(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        request_id = self._next_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        response = await self._send(payload)
        # Read off the RESPONSE, and case-insensitively, which httpx's Headers
        # already is: a server that spells it MCP-Session-Id is still honoured.
        # Set before parsing, so a session id that arrives alongside an error is
        # still carried by whatever the caller does next.
        self._session_id = response.headers.get("mcp-session-id") or self._session_id
        return self._parse(response, request_id)

    async def _call_streaming(self, method: str, params: dict, on_progress) -> dict:
        """응답을 스트림으로 읽으며 `notifications/progress`를 on_progress(문장)로 넘긴다.

        오래 걸리는 도구(인터넷 조사 30~70초)가 그동안 무엇을 하는지 보이게 하는 길이다. 서버가 JSON 한 덩어리로
        답하면 알림은 없고 결과만 온다 - 그것도 정상이다. 전체 시간 상한은 따로 건다: 스트림은 조각이 올 때마다
        httpx의 읽기 타임아웃이 새로 시작하므로, 알림만 끝없이 보내는 서버는 그것으로 못 막는다."""
        assert self._client is not None
        self._next_id += 1
        request_id = self._next_id
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        headers = {"Mcp-Session-Id": self._session_id} if self._session_id else {}
        try:
            async with asyncio.timeout(self.timeout):
                async with self._client.stream("POST", self.target.base_url, json=payload, headers=headers) as response:
                    if response.status_code >= 400:
                        raise MCPError(f"{UNREACHABLE_MESSAGE} (HTTP {response.status_code})")
                    self._session_id = response.headers.get("mcp-session-id") or self._session_id
                    if "text/event-stream" not in response.headers.get("content-type", ""):
                        await response.aread()
                        return self._parse(response, request_id)
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        try:
                            message = json.loads(line[len("data:") :].strip())
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(message, dict):
                            continue
                        if message.get("method") == "notifications/progress":
                            text = (message.get("params") or {}).get("message")
                            if isinstance(text, str) and text.strip():
                                # 남의 서버가 쓴 문장이 사용자 화면에 간다: 토큰을 지우고 길이를 자른다.
                                await on_progress(self._redact(text.strip())[:300])
                        elif message.get("id") == request_id:
                            if "error" in message:
                                detail = str((message["error"] or {}).get("message", ""))[:200]
                                raise MCPError(f"{PROTOCOL_MESSAGE} ({self._redact(detail)})")
                            return json.loads(self._redact(json.dumps(message.get("result") or {}, ensure_ascii=False)))
        except (httpx.HTTPError, TimeoutError) as exc:
            logger.warning("mcp transport error: %s", type(exc).__name__)
            raise MCPError(UNREACHABLE_MESSAGE) from exc
        raise MCPError(PROTOCOL_MESSAGE)

    async def _initialize(self) -> None:
        await self._call(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": CLIENT_INFO},
        )
        # The spec requires this notification before any other request, and a
        # strict server refuses tools/list without it. It carries no id, so
        # there is no response to parse - a 202 with an empty body is the normal
        # answer. Failures are not swallowed: _send already raises on 4xx/5xx,
        # and a handshake that cannot be completed is a server that cannot be
        # used.
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    async def list_tools(self) -> list[dict]:
        result = await self._call("tools/list")
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise MCPError(PROTOCOL_MESSAGE)
        return [tool for tool in tools if isinstance(tool, dict) and isinstance(tool.get("name"), str)]

    async def call_tool(self, name: str, arguments: dict, on_progress=None) -> ToolResult:
        """on_progress(문장)를 주면 서버에 진행 알림을 청하고(progressToken) 오는 대로 넘긴다."""
        if on_progress is None:
            result = await self._call("tools/call", {"name": name, "arguments": arguments})
        else:
            result = await self._call_streaming(
                "tools/call",
                {"name": name, "arguments": arguments, "_meta": {"progressToken": f"mopan-{self._next_id + 1}"}},
                on_progress,
            )
        parts: list[str] = []
        files: list[dict] = []
        documents: list[dict] = []
        blocks = [block for block in result.get("content") or [] if isinstance(block, dict)]
        source_links: set[int] = set()  # 문서의 출처로 쓰인 링크 블록의 자리 - 파일 칩으로 또 내지 않는다
        for position, block in enumerate(blocks):
            follower = blocks[position + 1] if position + 1 < len(blocks) else {}
            if position in source_links:
                continue
            if isinstance(block.get("text"), str):
                # 웹 페이지 주소가 바로 뒤따르는 text는 그 페이지의 본문이다. http(s)만 - 이 주소는 답변 밑의
                # 링크가 되므로 아래 파일 링크와 같은 검사를 통과해야 한다.
                if (
                    follower.get("type") == "resource_link"
                    and follower.get("mimeType") == "text/html"
                    and isinstance(follower.get("uri"), str)
                    and follower["uri"].startswith(("http://", "https://"))
                    and len(documents) < MAX_RESULT_FILES
                ):
                    name = follower.get("name")
                    source_links.add(position + 1)
                    documents.append(
                        {
                            "text": self._redact(block["text"])[:MAX_RESULT_CHARS],
                            "url": follower["uri"][:2000],
                            "name": self._redact(name)[:200] if isinstance(name, str) and name else follower["uri"][:200],
                        }
                    )
                else:
                    parts.append(block["text"])
            elif block.get("type") == "resource_link":
                # 도구가 만든 파일(예: maps의 클리핑 GeoTIFF). 서버 것 그대로 믿지 않는다:
                # 같은 origin 상대경로나 http(s)만 - javascript: 같은 것이 답변 밑의 링크가 되면 안 된다.
                uri, fname = block.get("uri"), block.get("name")
                if (
                    isinstance(uri, str)
                    and isinstance(fname, str)
                    and len(files) < MAX_RESULT_FILES
                    and (
                        uri.startswith("/")
                        and not uri.startswith("//")
                        or uri.startswith(("http://", "https://"))
                    )
                ):
                    files.append(
                        {
                            "uri": uri[:2000],
                            "name": self._redact(fname)[:200],
                            "mime_type": block.get("mimeType")
                            if isinstance(block.get("mimeType"), str)
                            else None,
                            "size_bytes": block.get("size") if isinstance(block.get("size"), int) else None,
                        }
                    )
        text = "\n".join(parts).strip()
        if not text:
            # structuredContent is the newer shape and some servers send only it.
            structured = result.get("structuredContent")
            if structured is not None:
                text = json.dumps(structured, ensure_ascii=False)
        # Redacted a second time: _parse already scrubbed the whole body, and this
        # covers the case where a future branch above builds text from something
        # that did not pass through it.
        text = self._redact(text)
        if result.get("isError"):
            failed = "MCP 도구 실행에 실패했습니다."
            raise MCPError(f"{failed} {text[:200]}" if text else failed)
        if len(text) > MAX_RESULT_CHARS:
            text = text[:MAX_RESULT_CHARS] + TRUNCATION_MARK
        return ToolResult(text, files, documents)
