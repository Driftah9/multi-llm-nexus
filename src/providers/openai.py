"""
OpenAI-compatible provider.

Works with any provider that implements the OpenAI chat completions API:
  OpenAI, Azure OpenAI, Groq, Mistral, DeepSeek, xAI/Grok, Together.ai,
  Fireworks.ai, Perplexity, Hugging Face, Cerebras, LM Studio, vLLM,
  llama.cpp server, Jan.ai, Ollama (/v1 endpoint).

Set base_url + api_key in config to point at any of them.
"""
import json
from typing import Optional

from .base import BaseProvider, Message, ProviderResponse, ToolCall, ToolResult

try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False


class OpenAIProvider(BaseProvider):
    """
    OpenAI API provider — also covers every OpenAI-compatible endpoint.

    Config keys:
      model        — model name (or deployment name for Azure)
      api_key      — API key (empty string for local providers like LM Studio)
      base_url     — override API endpoint (leave unset for standard OpenAI)
      api_version  — Azure API version header (e.g. "2024-02-01")
      max_tokens   — default 4096
      temperature  — default 0.7
      timeout      — request timeout in seconds, default 120
    """

    def __init__(self, config: dict):
        super().__init__(config)
        if not OPENAI_AVAILABLE:
            raise ImportError("openai package required: pip install openai")

        api_key: str = config.get("api_key", "") or "no-key"
        base_url: Optional[str] = config.get("base_url") or None
        api_version: Optional[str] = config.get("api_version") or None

        client_kwargs: dict = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        if api_version:
            client_kwargs["default_headers"] = {"api-version": api_version}

        self.client = AsyncOpenAI(**client_kwargs)
        self.max_tokens = config.get("max_tokens", 4096)
        self.temperature = config.get("temperature", 0.7)
        self.timeout = config.get("timeout", 120)

    async def send(self, messages: list[Message], system: str = "") -> ProviderResponse:
        oai_messages = self._convert_messages(messages, system)
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=oai_messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                timeout=self.timeout,
            )
            choice = response.choices[0]
            content = choice.message.content or ""
            tool_calls = []
            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}
                    tool_calls.append(ToolCall(
                        name=tc.function.name,
                        arguments=args,
                        call_id=tc.id,
                    ))
            usage = {}
            if response.usage:
                usage = {
                    "input_tokens": response.usage.prompt_tokens,
                    "output_tokens": response.usage.completion_tokens,
                }
            return ProviderResponse(content=content, tool_calls=tool_calls, usage=usage, raw=response)
        except Exception as e:
            return ProviderResponse(content=f"[error: {e}]")

    def _convert_messages(self, messages: list[Message], system: str) -> list[dict]:
        result = []
        if system:
            result.append({"role": "system", "content": system})
        for msg in messages:
            result.append({"role": msg.role, "content": msg.content})
        return result

    def supports_tools(self) -> bool:
        return True

    async def health_check(self) -> bool:
        try:
            await self.client.models.list()
            return True
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        """Query the provider's model list. Returns empty list on failure."""
        try:
            models = await self.client.models.list()
            return [m.id for m in models.data]
        except Exception:
            return []
