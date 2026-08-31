"""Pausing a plan for a human, and resuming it on a second request.

WHY A TOKEN AND A SECOND REQUEST, and not a generator held open until the user
answers. Three reasons, in the order they matter:

1. A held-open generator dies with the connection. The pause is the moment the
   user is most likely to walk away, reload, or lose a phone's network - and the
   thing that comes back would be a dead socket holding a half-run plan with a
   `write` tool already called and no answer ever produced.
2. SSE is one-way. The client cannot answer on the channel it is being asked on,
   so a second request exists either way; the only question is whether the
   server holds state in a generator's stack frame or in a store with a TTL.
3. The state has to outlive one uvicorn worker. A generator on worker A cannot
   be resumed by a request that load-balances onto worker B.

WHAT MAKES A TOKEN UNFORGEABLE AND UNREPLAYABLE:

- `secrets.token_urlsafe(32)` - 256 bits from the OS CSPRNG, the same source as
  a session id. Guessing one is not a threat model.
- The token names a Redis key that must EXIST. There is nothing to forge: the
  payload lives server-side, the client holds an opaque string.
- The key is read with `GETDEL`, one round trip that reads and deletes
  atomically. A replay - the same token sent twice, by the same user or by
  someone who intercepted it - finds nothing and is refused. A double-clicked
  승인 button therefore approves once, which is the whole point of a gate in
  front of a destructive call.
- The payload records the user who was asked. Another user's token is refused
  with the same 404 a nonexistent one gets, so it cannot be used to probe.

WHAT IS DELIBERATELY NOT STORED: the MCP auth token, or anything else resolved
from the database. The payload keeps the plan as NAMES, and the resume re-loads
the catalogue and re-validates against it - so a tool an admin disabled during
the pause is refused on resume exactly as a fresh plan naming it would be.
"""

import json
import logging
import secrets
import uuid

from redis.asyncio import Redis

logger = logging.getLogger("mopan.orchestrator")

KEY_PREFIX = "mopan:approval:"
# The one message the user sees for a token that is unknown, expired, already
# used, or someone else's. Same string for all four, for the reason
# get_owned_conversation answers 404 rather than 403: distinguishing them tells
# a holder of a guessed token which guess was closer.
APPROVAL_NOT_FOUND_MESSAGE = "승인 요청을 찾을 수 없거나 이미 처리되었습니다. 질문을 다시 보내 주세요."


def new_token() -> str:
    return secrets.token_urlsafe(32)


async def store_pending(redis: Redis, payload: dict, *, ttl_seconds: int) -> str:
    """Returns the token the client sends back. `default=str` because the payload
    carries uuids (conversation and collection ids) that JSON cannot hold."""
    token = new_token()
    await redis.set(
        KEY_PREFIX + token,
        json.dumps(payload, ensure_ascii=False, default=str),
        ex=ttl_seconds,
    )
    return token


async def consume_pending(redis: Redis, token: str, user_id: uuid.UUID) -> dict | None:
    """Read and delete in one atomic operation, then check the owner.

    The delete is unconditional and happens BEFORE the ownership check on
    purpose: a token someone else's request touched is burned either way, so a
    stolen token cannot be probed against user after user.
    """
    if not token:
        return None
    raw = await redis.getdel(KEY_PREFIX + token)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:  # pragma: no cover - we wrote it
        logger.warning("approval payload was not JSON")
        return None
    if not isinstance(payload, dict) or payload.get("user_id") != str(user_id):
        logger.warning("approval token presented by a different user")
        return None
    return payload
