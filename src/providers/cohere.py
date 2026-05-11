"""
Cohere provider — Command R and Command R+.

Best use case: RAG / retrieval-augmented workflows.
Not recommended as a general-purpose primary — use as a specialist.

Install: pip install cohere
"""
from typing import Optional

from .base import BaseProvider, Message, ProviderResponse, ToolCall

try:
    import cohere
    COHERE_AVAILABLE = True
except ImportError:
    COHERE_AVAILABLE = False


class CohereProvider(BaseProvider):
    """
    Cohere provider using the native Cohere SDK.

    Config keys:
      model        — e.g. "command-r", "command-r-plus"
      api_key      — COHERE_API_KEY
      max_tokens   — default 4096
      temperature  — default 0.7
      preamble     — optional system preamble (Cohere's term for system prompt)
    """

    def __init__(self, config: dict):
        super().__init__(config)
        if not COHERE_AVAILABLE:
            raise ImportError("cohere package required: pip install cohere")
        self._client = cohere.AsyncClientV2(api_key=config.get("api_key", ""))
        self.max_tokens = config.get("max_tokens", 4096)
        self.temperature = config.get("temperature", 0.7)
        self._preamble: Optional[str] = config.get("preamble") or None

    def _convert_messages(self, messages: list[Message], system: str) -> list[dict]:
        result = []
        for msg in messages:
            role = "assistant" if msg.role == "assistant" else "user"
            result.append({"role": role, "content": msg.content})
        return result

    async def send(self, messages: list[Message], system: str = "") -> ProviderResponse:
        chat_messages = self._convert_messages(messages, system)
        effective_preamble = system or self._preamble

        kwargs: dict = {
            "model": self.model,
            "messages": chat_messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        if effective_preamble:
            # Cohere v2 uses a system message as first entry
            kwargs["messages"] = [
                {"role": "system", "content": effective_preamble},
                *chat_messages,
            ]

        response = await self._client.chat(**kwargs)
        content = ""
        tool_calls = []

        for block in response.message.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_call":
                tc = block.tool_call
                tool_calls.append(ToolCall(
                    name=tc.name,
                    arguments=tc.parameters or {},
                    call_id=getattr(tc, "id", None),
                ))

        usage = {}
        if response.usage:
            usage = {
                "input_tokens": getattr(response.usage.billed_units, "input_tokens", 0),
                "output_tokens": getattr(response.usage.billed_units, "output_tokens", 0),
            }

        return ProviderResponse(content=content, tool_calls=tool_calls, usage=usage, raw=response)

    def supports_tools(self) -> bool:
        return True

    async def health_check(self) -> bool:
        try:
            response = await self._client.chat(
                model=self.model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
            )
            return bool(response.message.content)
        except Exception:
            return False
