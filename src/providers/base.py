from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class ToolCall:
    name: str
    arguments: dict
    call_id: Optional[str] = None


@dataclass
class ToolResult:
    call_id: Optional[str]
    content: str
    is_error: bool = False


@dataclass
class Message:
    role: str  # "user", "assistant", "system", "tool"
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class ProviderResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    raw: Any = None


class BaseProvider(ABC):
    """
    Abstract LLM provider. Implement this to add any model to the Nexus.
    The router selects which provider handles each task based on providers.yaml.
    """

    def __init__(self, config: dict):
        self.config = config
        self.model = config.get("model", "")

    @abstractmethod
    async def send(self, messages: list[Message], system: str = "") -> ProviderResponse:
        """Send a conversation to the LLM and return the response."""
        ...

    @abstractmethod
    def supports_tools(self) -> bool:
        """Whether this provider supports tool/function calling."""
        ...

    def format_tool_call(self, tool_name: str, args: dict) -> dict:
        """Format a tool call in this provider's native format. Override if needed."""
        return {"name": tool_name, "arguments": args}

    def parse_tool_response(self, raw: Any) -> ToolResult:
        """Parse a raw tool response into a ToolResult. Override if needed."""
        return ToolResult(call_id=None, content=str(raw))

    @abstractmethod
    async def health_check(self) -> bool:
        """Verify the provider is reachable and the model is available."""
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.model})"
