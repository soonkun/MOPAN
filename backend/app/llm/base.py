from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class LLMError(RuntimeError):
    """Domain error wrapping any provider SDK failure, so callers never have to
    import openai to handle an error."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class ChatMessage:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    name: str | None = None
    tool_call_id: str | None = None

    def to_openai(self) -> dict:
        payload: dict = {"role": self.role, "content": self.content}
        if self.name is not None:
            payload["name"] = self.name
        if self.tool_call_id is not None:
            payload["tool_call_id"] = self.tool_call_id
        return payload


@dataclass
class ChatResult:
    content: str
    usage: dict = field(default_factory=dict)
    model: str = ""
    # Slice 2 (MCP) populates this. Slice 1 always passes tools=None and ignores
    # the field; declaring it now keeps the ABC stable across the slice boundary.
    tool_calls: list[ToolCall] | None = None


class LLMProvider(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        tools: list[dict] | None = None,
        **kwargs,
    ) -> ChatResult: ...
