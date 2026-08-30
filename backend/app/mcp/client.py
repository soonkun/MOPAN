import ipaddress
import json
import logging
import socket
from dataclasses import dataclass
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

    async def call_tool(self, name: str, arguments: dict) -> str:
        result = await self._call("tools/call", {"name": name, "arguments": arguments})
        parts: list[str] = []
        for block in result.get("content") or []:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
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
        return text
