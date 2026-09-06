import asyncio
import logging
import time

from openai import AsyncOpenAI, OpenAIError, RateLimitError

from app.core.config import EMBEDDING_MAX_BATCH_SIZE
from app.core.logging import log_event
from app.llm.base import ChatMessage, ChatResult, LLMError, LLMProvider, ToolCall

# 임베딩 배치가 429를 만났을 때의 바깥 재시도 횟수. 지수 백오프(2,4,8,…,45초
# 상한)로 총 3분 남짓 - PIPELINE_TIMEOUT(870초) 안에서 분당 한도 창 몇 번을
# 넘길 수 있는 크기다.
_RATE_LIMIT_TRIES = 8

logger = logging.getLogger("mopan.llm")


class OpenAIProvider(LLMProvider):
    def __init__(
        self,
        api_key: str,
        embedding_model: str,
        answer_model: str,
        *,
        # Settings is the source of truth for all four; both construction sites
        # pass them explicitly. These defaults exist only so tests can build a
        # provider without restating them - keep them in step with config.py.
        timeout: float = 30.0,
        max_retries: int = 3,
        batch_size: int = 128,
        batch_chars: int = 200_000,
        embedding_dim: int | None = None,
    ):
        # Both are admin-configurable, so an invalid value is reachable from
        # configuration. Unvalidated, batch_size <= 0 degrades to one request per
        # chunk - no error, just a cost and latency blowup - and a value above
        # the endpoint's 2048-element cap is rejected mid-document.
        if not 1 <= batch_size <= EMBEDDING_MAX_BATCH_SIZE:
            raise ValueError(f"batch_size must satisfy 1 <= value <= {EMBEDDING_MAX_BATCH_SIZE}")
        if batch_chars < 1:
            raise ValueError("batch_chars must be at least 1")
        # Explicit timeout and retries. The SDK default is 600s, and one hung
        # embedding call would occupy an arq worker slot for ten minutes.
        # Measured on openai 1.47.0: the SDK's own retry loop covers 408, 409,
        # 429 and 5xx plus connection timeouts, and does NOT retry 401 or 400 -
        # so a bad key costs one attempt, not max_retries of them. Nothing to
        # reimplement here; just hand it the budget from Settings.
        self.client = AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=max_retries)
        self.embedding_model = embedding_model
        self.answer_model = answer_model
        self.batch_size = batch_size
        self.batch_chars = batch_chars
        self.embedding_dim = embedding_dim

    def _batches(self, texts: list[str]) -> list[list[str]]:
        """OpenAI's embeddings endpoint caps at 2048 array elements and roughly
        300k tokens per request. One request per document blows both on a large
        PDF, so split on item count and on a character budget.

        Characters are a proxy for tokens and the ratio is script-dependent.
        Measured with cl100k at 200_000 characters: ASCII ~44k tokens, realistic
        Korean prose ~168k, spaced Hangul ~225k, CJK han ~260k, and unspaced
        Hangul - a glossary or a table column - ~286k. That worst case clears the
        ~300k ceiling by 5%, not by the comfortable margin the spaced sample
        suggests.
        # ponytail: an emoji-dominated document reaches ~550k tokens in the same
        # 200_000 characters and would be rejected by the endpoint. It fails
        # loudly as an LLMError rather than corrupting anything; ChunkCandidate
        # already carries token_count, so budget on that if 5% ever proves thin.

        A single input longer than batch_chars still goes out alone rather than
        being dropped - it cannot be split here without breaking the caller's
        text/vector correspondence, and MAX_CHUNK_TOKENS already bounds every
        chunk to at most half the 8191-token per-input limit.
        """
        batches: list[list[str]] = []
        current: list[str] = []
        current_chars = 0
        for text in texts:
            if current and (len(current) >= self.batch_size or current_chars + len(text) > self.batch_chars):
                batches.append(current)
                current, current_chars = [], 0
            current.append(text)
            current_chars += len(text)
        if current:
            batches.append(current)
        return batches

    def _vectors_in_input_order(self, response, expected: int) -> list[list[float]]:
        """Unpack one embeddings response, in the order the inputs were sent.

        Measured on openai 1.47.0: the SDK returns response.data in whatever
        order the server wrote it - a server that reverses the array yields
        indices [2, 1, 0] - and it does not check the array length, so three
        inputs and a one-item response parse without error. Either one pairs a
        chunk with another chunk's vector, and after ingest that is invisible
        and unrecoverable. Sort on the per-item index the wire format carries
        for exactly this reason, and refuse anything that is not a complete
        0..n-1 cover.
        """
        data = sorted(response.data, key=lambda item: item.index)
        if [item.index for item in data] != list(range(expected)):
            raise LLMError(
                f"embedding response does not cover inputs 0..{expected - 1} exactly "
                f"({len(data)} vectors returned)"
            )
        vectors = [item.embedding for item in data]
        if self.embedding_dim is not None:
            width = next((len(v) for v in vectors if len(v) != self.embedding_dim), None)
            if width is not None:
                # EMBEDDING_MODEL and EMBEDDING_DIM are independent settings, and
                # the mismatch otherwise surfaces as a pgvector insert failure
                # after the whole document has already been paid for.
                raise LLMError(
                    f"{self.embedding_model} returned {width}-dimension vectors, "
                    f"but EMBEDDING_DIM is {self.embedding_dim}"
                )
        return vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        started = time.perf_counter()
        vectors: list[list[float]] = []
        try:
            # `dimensions` is what lets EMBEDDING_MODEL move to a wider model
            # WITHOUT a migration. text-embedding-3-* are Matryoshka models: the
            # parameter truncates to the first N dimensions and renormalises, and
            # on this corpus text-embedding-3-large at 1536 measured IDENTICALLY
            # to the full 3072 (anchor@14 0.904, recall 1.000) - so the existing
            # vector(1536) column is not a constraint on model choice.
            #
            # Only sent for models that accept it. text-embedding-ada-002 rejects
            # the parameter outright, and a provider that silently dropped it
            # would return 1536-wide vectors that happen to match EMBEDDING_DIM
            # while being a different model's - which the width check below
            # cannot catch.
            extra = (
                {"dimensions": self.embedding_dim}
                if self.embedding_dim is not None
                and self.embedding_model.startswith("text-embedding-3-")
                else {}
            )
            for batch in self._batches(texts):
                # 분당 토큰 한도(429)는 오류가 아니라 큰 표를 올린 날의 정상
                # 상태다 - 창은 길어야 60초면 되살아난다. SDK 내부 재시도
                # (max_retries=3)는 초 단위 백오프라 만행짜리 문서에서 금방
                # 바닥났고(실사고: 등록농약 10,001행 xlsx가 failed), 그래서
                # 여기서만 배치 단위로 길게 기다린다. 429가 아닌 오류는 그대로
                # 실패한다 - 인내는 한도에만, 장애에는 아니다.
                for attempt in range(_RATE_LIMIT_TRIES):
                    try:
                        response = await self.client.embeddings.create(
                            model=self.embedding_model, input=batch, **extra
                        )
                        break
                    except RateLimitError:
                        if attempt == _RATE_LIMIT_TRIES - 1:
                            raise
                        await asyncio.sleep(min(2 * 2**attempt, 45))
                vectors.extend(self._vectors_in_input_order(response, len(batch)))
        except OpenAIError as exc:
            # str(exc) on every SDK error class is "Error code: N - {server body}"
            # or "Request timed out."; verified not to carry the API key or a
            # traceback. Routers still must not echo this to a client.
            raise LLMError(f"embedding request failed: {exc}") from exc

        log_event(
            logger,
            "embeddings_created",
            model=self.embedding_model,
            count=len(texts),
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return vectors

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        tools: list[dict] | None = None,
        **kwargs,
    ) -> ChatResult:
        started = time.perf_counter()
        request: dict = {
            "model": self.answer_model,
            "messages": [m.to_openai() for m in messages],
            "temperature": temperature,
            **kwargs,
        }
        if tools:
            request["tools"] = tools
        # 추론 계열(gpt-5·o시리즈)은 temperature를 기본값 외에 받지 않고,
        # reasoning_effort는 그 계열만 받는다. 어느 쪽이든 어기면 원문 400이
        # 그대로 나가므로 적응은 호출부가 아니라 여기 한 곳에서 한다 -
        # 비추론 모델에 남은 effort(브라우저에 기억된 값)는 조용히 버린다.
        from app.core.config import model_supports_reasoning

        if model_supports_reasoning(str(request["model"])):
            request.pop("temperature", None)
        else:
            request.pop("reasoning_effort", None)
        effort = request.pop("reasoning_effort", None)
        if effort is not None:
            # extra_body로: 이 컨테이너의 openai SDK는 reasoning_effort를 명명
            # 인자로 모른다(실측 TypeError). extra_body는 버전 무관하게 요청
            # JSON에 병합된다.
            request["extra_body"] = {**request.get("extra_body", {}), "reasoning_effort": effort}

        try:
            response = await self.client.chat.completions.create(**request)
        except OpenAIError as exc:
            raise LLMError(f"chat completion failed: {exc}") from exc

        # LLMError promises callers never have to import openai to handle a
        # failure, and this abstraction exists to front OpenAI-compatible and
        # local endpoints, where a non-conforming body is far likelier than it is
        # from OpenAI. Unpacking a malformed response must not escape as an
        # IndexError/AttributeError and surface as an unhandled 500.
        if not response.choices:
            raise LLMError("chat completion returned no choices")
        choice = response.choices[0]
        raw_tool_calls = getattr(choice.message, "tool_calls", None) or []
        try:
            tool_calls = [
                ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
                for tc in raw_tool_calls
            ] or None
        except AttributeError as exc:
            raise LLMError(f"chat completion returned a malformed tool call: {exc}") from exc

        log_event(
            logger,
            "chat_completion",
            model=response.model,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return ChatResult(
            content=choice.message.content or "",
            usage=response.usage.model_dump() if response.usage else {},
            model=response.model,
            tool_calls=tool_calls,
        )

    async def aclose(self) -> None:
        await self.client.close()
