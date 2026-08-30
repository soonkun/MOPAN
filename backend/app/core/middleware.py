import logging
import time
import uuid

from app.core.db import current_sessionmaker
from app.core.logging import log_event, request_id_var

logger = logging.getLogger("mopan.request")


class RequestContextMiddleware:
    """Pure-ASGI (not BaseHTTPMiddleware) so SSE responses stream unimpeded."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())
        token = request_id_var.set(request_id)
        # scope["app"] is set by Starlette.__call__ BEFORE the middleware stack
        # runs, and the lifespan has filled state.sessionmaker by the time any
        # http scope arrives - but getattr, not indexing: an app started without
        # its lifespan (a bare ASGI mount, an early smoke test) must not 500 every
        # request over a prompt lookup that has a fallback anyway.
        sessionmaker_token = current_sessionmaker.set(
            getattr(getattr(scope.get("app"), "state", None), "sessionmaker", None)
        )
        started = time.perf_counter()
        state = {"status": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                message["headers"] = list(message.get("headers", [])) + [
                    (b"x-request-id", request_id.encode())
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            log_event(
                logger,
                "http_request",
                method=scope.get("method"),
                path=scope.get("path"),
                status=state["status"],
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            request_id_var.reset(token)
            current_sessionmaker.reset(sessionmaker_token)
