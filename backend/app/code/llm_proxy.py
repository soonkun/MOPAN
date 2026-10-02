"""OpenCode가 보는 유일한 모델 서버 - OpenAI 호환 `/api/code/llm/v1`.

샌드박스 안의 opencode(서버 작업 공간)도, 사용자 PC의 opencode(내 컴퍼터 연결)도 모델은 여기로만
부른다. 이유 셋: (1) 카탈로그 허용 목록이 /api/chat과 같은 경계로 지켜진다, (2) 자는 vLLM을 깨우는
것은 우리 프로세스의 일이다(실측: 잠든 8001에 직접 보낸 요청은 응답 없이 타임아웃 - opencode가
"SSE read timed out"으로 재시도만 반복했다), (3) 사용자 PC는 vLLM 포트에 닿을 수 없다.

인증은 Bearer 토큰: 서버 프로세스마다 발급한 토큰(runtime.tokens) 또는 내 컴퓨터 연결 토큰(해시).
요청 본문은 손대지 않고 그대로 넘긴다 - 스트리밍(SSE)도 바이트 그대로.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_app_settings
from app.core.db import get_db_session
from app.llm.catalog import load_catalog
from app.llm.sleep import ensure_awake
from app.models.code import CodeProfile

logger = logging.getLogger("mopan.code.llm")
router = APIRouter(prefix="/api/code/llm/v1", tags=["code"])


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def code_models(catalog, settings: Settings) -> list:
    """코드 탭이 쓸 수 있는 모델. CODE_LOCAL_ONLY면 provider=local(이 서버의 vLLM·Ollama)만 - 소유자 원칙:
    코드와 자료가 OpenAI·Anthropic으로 나가지 않는다. 채팅의 카탈로그는 그대로다."""
    return [m for m in catalog.enabled if not settings.code_local_only or m.provider == "local"]


def _limit(context: int) -> dict[str, int]:
    return {"context": context, "output": min(32_768, max(1024, context // 4))}


async def model_limit(request: Request, settings: Settings, model_id: str) -> dict[str, int]:
    """opencode에 알려 주는 창 크기. output은 반드시 context보다 작아야 한다.

    실측(2026-09-24): context 16384에 output 32768을 주자 남은 창이 음수가 되어 매 단계 자동 압축(compaction)
    → "계속" → 압축 … 1,572메시지 루프. vLLM이 내는 모델은 그 서버의 max_model_len을 그대로 쓰고(gemma4 16k,
    qwen3.8 128k처럼 서버마다 다르다), 나머지(OpenAI)는 CODE_MODEL_CONTEXT."""
    provider = request.app.state.llm_provider
    base = provider._vllm_base(model_id)
    if base is None:
        return _limit(settings.code_model_context)
    runtime = request.app.state.code_runtime
    cache: dict[str, dict[str, int]] = runtime.__dict__.setdefault("vllm_context", {})
    if base not in cache:
        try:
            r = await runtime.http.get(f"{base.rstrip('/')}/models", timeout=5.0)
            cache[base] = {
                m["id"]: int(m["max_model_len"]) for m in r.json().get("data", []) if m.get("max_model_len")
            }
        except Exception:  # noqa: BLE001 - 발견 실패는 기본값으로, 다음 요청에 다시 시도
            return _limit(settings.code_model_context)
    return _limit(cache[base].get(model_id, settings.code_model_context))


async def _user_for_token(request: Request, db: AsyncSession) -> uuid.UUID:
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="토큰이 없습니다.")
    user_id = request.app.state.code_runtime.tokens.get(token)
    if user_id is not None:
        return user_id
    user_id = await db.scalar(
        select(CodeProfile.user_id).where(CodeProfile.bridge_token_hash == token_hash(token))
    )
    if user_id is None:
        raise HTTPException(status_code=401, detail="토큰이 올바르지 않습니다.")
    return user_id


def _usage_from_body(raw: bytes) -> dict[str, int]:
    """응답 전체(JSON 하나 또는 SSE 청크들)에서 usage를 뽑는다. SSE는 뒤에서부터 - usage는 마지막 data: 줄에 있다."""
    text = raw.decode("utf-8", "replace")
    if not text.lstrip().startswith("data:"):
        chunks = [text]
    else:
        chunks = [ln[5:].strip() for ln in reversed(text.splitlines()) if ln.startswith("data:")]
    for c in chunks:
        if not c or c == "[DONE]":
            continue
        try:
            u = json.loads(c).get("usage")
        except (ValueError, AttributeError):
            continue
        if isinstance(u, dict) and (u.get("prompt_tokens") or u.get("completion_tokens")):
            return {k: int(u.get(k) or 0) for k in ("prompt_tokens", "completion_tokens")}
    return {}


async def _add_usage(session: AsyncSession, user_id: uuid.UUID, usage: dict[str, int]) -> None:
    profile = await session.get(CodeProfile, user_id, with_for_update=True)
    if profile is None:
        return
    total = dict(profile.usage or {})
    for k, v in usage.items():
        total[k] = int(total.get(k) or 0) + v
    profile.usage = total
    await session.commit()


@router.get("/models")
async def list_models(
    request: Request,
    settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db_session, scope="function"),
):
    await _user_for_token(request, db)
    catalog = await load_catalog(db, settings)
    catalog.apply_to_provider(request.app.state.llm_provider)
    # context/output은 OpenAI 스키마 밖의 덧붙임 - 컴패니언(mopan-code.mjs)이 opencode limit에 쓴다.
    data = []
    for m in code_models(catalog, settings):
        limit = await model_limit(request, settings, m.id)
        data.append({"id": m.id, "object": "model", "owned_by": "mopan", **limit})
    return {"object": "list", "data": data}


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    settings: Settings = Depends(get_app_settings),
    db: AsyncSession = Depends(get_db_session, scope="function"),
):
    user_id = await _user_for_token(request, db)
    body = await request.json()
    model = str(body.get("model") or "")
    catalog = await load_catalog(db, settings)
    if not catalog.is_allowed(model) or model not in {m.id for m in code_models(catalog, settings)}:
        return JSONResponse(
            status_code=400,
            content={
                "error": {"message": f"허용되지 않은 모델입니다: {model}", "type": "invalid_request_error"}
            },
        )
    provider = request.app.state.llm_provider
    catalog.apply_to_provider(provider)
    # 같은 라우팅 표(app/llm/openai_provider.py:_client_for) - vLLM/Ollama/OpenAI 중 그 모델을 내는 곳.
    client = provider._client_for(model)
    vllm_base = provider._vllm_base(model)
    if vllm_base is not None:
        await ensure_awake(vllm_base)
        # opencode는 config의 limit.output을 max_tokens로 보내는데 vLLM은 max_model_len을 넘는 값을 400으로
        # 거절한다(실측: 32000 > 16384). 빼면 vLLM이 남은 창을 스스로 계산한다.
        body.pop("max_tokens", None)
        body.pop("max_completion_tokens", None)
    # 사용자별 토큰 사용량(소유자 요청 2026-09-28). 스트림에서는 마지막 청크에만 usage가 실리고, OpenAI 규격은
    # stream_options.include_usage를 켜야 보낸다(vLLM·Ollama도 따른다). 지나가는 바이트를 복사해 두었다가 끝에 한 번 더한다.
    if body.get("stream"):
        body["stream_options"] = {**(body.get("stream_options") or {}), "include_usage": True}
    url = str(client.base_url).rstrip("/") + "/chat/completions"
    upstream: httpx.AsyncClient = request.app.state.code_runtime.http
    req = upstream.build_request(
        "POST", url, json=body, headers={"Authorization": f"Bearer {client.api_key}"}
    )
    resp = await upstream.send(req, stream=True)

    sessionmaker = request.app.state.sessionmaker

    async def iterate():
        seen = bytearray()
        try:
            async for chunk in resp.aiter_raw():
                seen += chunk
                yield chunk
        finally:
            await resp.aclose()
            try:
                usage = _usage_from_body(bytes(seen))
                if usage:
                    async with sessionmaker() as session:
                        await _add_usage(session, user_id, usage)
            except Exception:  # noqa: BLE001 - 집계 실패가 답변을 깨면 안 된다
                logger.exception("code_usage_record_failed")

    content_type = resp.headers.get("content-type", "application/json")
    headers = (
        {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}
        if "event-stream" in content_type
        else {}
    )
    return StreamingResponse(
        iterate(), status_code=resp.status_code, media_type=content_type, headers=headers
    )
