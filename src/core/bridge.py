"""
Provider bridge — the unified invoke interface for all adapters.

Adapters call bridge.invoke(prompt, session_key, tier) and get back
a BridgeResult(text, cost). The bridge handles:
  - Routing to the correct provider via the Router
  - Session/message-history management per provider type
  - Response formatting

Claude Code CLI is a special case: it manages its own session_id and
handles multi-turn history internally. Every other provider requires
Nexus to maintain message history explicitly.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .session import SessionStore
from .router import Router
from ..providers.base import Message, ProviderResponse

logger = logging.getLogger(__name__)

# Max messages to keep in history per session (for non-claude_code providers)
MAX_HISTORY = 40


@dataclass
class BridgeResult:
    text: str
    session_id: Optional[str] = None   # claude_code session ID for resumption
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    provider_type: str = ""
    elapsed: float = 0.0


class NexusBridge:
    """
    Provider-agnostic invoke interface.

    Adapters don't know which LLM they're talking to. They call invoke()
    and get back a BridgeResult. The bridge picks the provider via the
    router, manages history, and returns a normalized result.

    For claude_code: session_id is stored in SessionStore and passed
    back to the CLI to resume context between calls.

    For all other providers: message history is accumulated in SessionStore
    and sent with each request (stateless API pattern).
    """

    def __init__(self, router: Router, sessions: SessionStore, system_prompt: str = ""):
        self.router = router
        self.sessions = sessions
        self.system_prompt = system_prompt
        # In-memory message history per session key, for non-claude_code providers
        self._history: dict[str, list[Message]] = {}

    async def invoke(
        self,
        prompt: str,
        session_key: str,
        tier: Optional[str] = None,
        task_type: Optional[str] = None,
        on_output: Optional[Callable[[str], None]] = None,
    ) -> BridgeResult:
        """
        Send a prompt and return the response.

        Args:
            prompt: The user message text (may include platform prefix)
            session_key: Unique key for this conversation (e.g. "mm_channel123")
            tier: Force a tier (nano/standard/deep). If None, uses routing config.
            task_type: Force a task type for routing (code/privacy/etc.)
            on_output: Optional callback when output starts arriving (for status updates)

        Returns:
            BridgeResult with response text and metadata
        """
        start = time.time()

        provider = self.router.route(prompt, task_type=tier or task_type)
        provider_type = type(provider).__name__.lower().replace("provider", "")

        try:
            if provider_type == "claudecode":
                result = await self._invoke_claude_code(provider, prompt, session_key, on_output)
            else:
                result = await self._invoke_api_provider(provider, prompt, session_key, provider_type)
        except Exception as e:
            logger.error(f"Bridge invoke error ({provider_type}): {e}")
            result = BridgeResult(text=f"_(error: {e})_", provider_type=provider_type)

        result.elapsed = time.time() - start
        result.provider_type = provider_type
        return result

    async def _invoke_claude_code(self, provider, prompt: str, session_key: str, on_output) -> BridgeResult:
        """Claude Code CLI — session_id-based resumption, MCP tools."""
        session_id = self.sessions.get(session_key)

        # Inject session_id into config if present
        if session_id:
            provider.config["resume_session"] = session_id

        if on_output:
            provider.config["on_output"] = on_output

        response: ProviderResponse = await provider.send(
            [Message(role="user", content=prompt)],
            system=self.system_prompt,
        )

        new_session_id = None
        if response.raw and isinstance(response.raw, list):
            for item in response.raw:
                if isinstance(item, dict) and item.get("session_id"):
                    new_session_id = item["session_id"]
                    break

        if new_session_id:
            await self.sessions.set(session_key, new_session_id)

        usage = response.usage or {}
        cost = self._estimate_cost_claude(
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
            "sonnet",
        )

        return BridgeResult(
            text=response.content,
            session_id=new_session_id,
            cost_usd=cost,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )

    async def _invoke_api_provider(self, provider, prompt: str, session_key: str, provider_type: str) -> BridgeResult:
        """All API providers — explicit message history management."""
        history = self._history.setdefault(session_key, [])

        # Add the new user message
        history.append(Message(role="user", content=prompt))

        # Trim to max history (keep pairs)
        if len(history) > MAX_HISTORY:
            self._history[session_key] = history[-MAX_HISTORY:]
            history = self._history[session_key]

        response: ProviderResponse = await provider.send(
            history,
            system=self.system_prompt,
        )

        # Add assistant response to history
        if response.content:
            history.append(Message(role="assistant", content=response.content))

        usage = response.usage or {}
        cost = self._estimate_cost_generic(
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
        )

        return BridgeResult(
            text=response.content,
            cost_usd=cost,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )

    def clear_session(self, session_key: str) -> None:
        """Clear history and session ID for a session key."""
        self._history.pop(session_key, None)

    def get_history_length(self, session_key: str) -> int:
        return len(self._history.get(session_key, []))

    @staticmethod
    def _estimate_cost_claude(input_tokens: int, output_tokens: int, tier: str) -> float:
        """Rough Claude cost estimate (USD). Actual billing may differ."""
        rates = {
            "haiku":  (0.0008, 0.004),
            "sonnet": (0.003,  0.015),
            "opus":   (0.015,  0.075),
        }
        inp, out = rates.get(tier, rates["sonnet"])
        return (input_tokens / 1_000_000) * inp + (output_tokens / 1_000_000) * out

    @staticmethod
    def _estimate_cost_generic(input_tokens: int, output_tokens: int) -> float:
        """Generic cost estimate for unknown providers — uses ballpark rates."""
        return (input_tokens / 1_000_000) * 0.002 + (output_tokens / 1_000_000) * 0.008
