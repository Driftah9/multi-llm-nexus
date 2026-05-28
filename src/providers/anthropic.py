"""
Anthropic API provider — direct API, no CLI dependency.
Use this when you want Claude without requiring Claude Code installed.
Supports prompt caching, extended thinking, and tool use.
"""
import json
from typing import Optional

from .base import BaseProvider, Message, ProviderResponse, ToolCall

try:
    import anthropic as anthropic_sdk
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


class AnthropicProvider(BaseProvider):
    """
    Direct Anthropic API provider.
    Install: pip install anthropic
    """

    def __init__(self, config: dict):
        super().__init__(config)
        if not ANTHROPIC_AVAILABLE:
            raise ImportError("anthropic package required: pip install anthropic")
        self.client = anthropic_sdk.AsyncAnthropic(
            api_key=config.get("api_key", "")
        )
        self.max_tokens = config.get("max_tokens", 8096)
        self.use_cache = config.get("prompt_caching", True)
        # How many trailing messages to leave uncached (the live tail).
        # Everything before the tail gets a cache breakpoint so stable history
        # accumulates cache reads rather than being re-sent uncached each turn.
        self.cache_history_tail = config.get("cache_history_tail", 2)

    async def send(self, messages: list[Message], system: str = "") -> ProviderResponse:
        sdk_messages = self._convert_messages(messages)
        system_blocks = self._build_system(system)
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_blocks,
            messages=sdk_messages,
        )
        content = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content = block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    name=block.name,
                    arguments=block.input,
                    call_id=block.id
                ))
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_read": getattr(response.usage, "cache_read_input_tokens", 0),
            "cache_write": getattr(response.usage, "cache_creation_input_tokens", 0),
        }
        return ProviderResponse(content=content, tool_calls=tool_calls, usage=usage, raw=response)

    def _build_system(self, system: str) -> list[dict] | str:
        if not system:
            return ""
        if self.use_cache:
            return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        return system

    def _convert_messages(self, messages: list[Message]) -> list[dict]:
        result = []
        for msg in messages:
            if msg.role in ("user", "assistant"):
                result.append({"role": msg.role, "content": msg.content})

        # Second cache breakpoint: stable conversation history.
        # Mark the message just before the live tail so every turn pays a cache
        # write for 1 new message and gets cache reads for all prior stable history.
        if self.use_cache and len(result) > self.cache_history_tail + 1:
            boundary_idx = len(result) - self.cache_history_tail - 1
            msg = result[boundary_idx]
            text = msg["content"]
            if isinstance(text, str):
                msg["content"] = [
                    {"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}
                ]
            elif isinstance(text, list):
                for block in reversed(text):
                    if isinstance(block, dict) and block.get("type") == "text":
                        block["cache_control"] = {"type": "ephemeral"}
                        break

        return result

    def supports_tools(self) -> bool:
        return True

    async def health_check(self) -> bool:
        try:
            response = await self.client.messages.create(
                model=self.model,
                max_tokens=10,
                messages=[{"role": "user", "content": "ping"}]
            )
            return True
        except Exception:
            return False
