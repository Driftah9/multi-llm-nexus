"""
Provider bridge — the unified invoke interface for all adapters.

Adapters call bridge.invoke(prompt, session_key, tier) and get back
a BridgeResult(text, cost). The bridge handles:
  - Routing to the correct provider via the Router OR ProviderChain
  - Automatic failover to next provider on error
  - Session/message-history management per provider type
  - Response formatting

Claude Code CLI is a special case: it manages its own session_id and
handles multi-turn history internally. Every other provider requires
Nexus to maintain message history explicitly.

ProviderChain enables multi-provider failover: if primary (Claude) fails,
automatically try secondary (Gemini), then tertiary (Ollama).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .cache_utils import normalize_for_cache
from .security import AuthorizationGate, SecurityPolicy
from .session import SessionStore
from .router import Router
from .provider_chain import ProviderChain, ProviderChainEntry
from .heartbeat import HeartbeatManager
from ..providers.base import Message, ProviderResponse, BaseProvider

# Optional imports — only used when pool routing is active
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .pool_router import PoolRouter
    from .pool_manager import PoolManager
    from .triage import TriageResult

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
    Provider-agnostic invoke interface with automatic failover.

    Adapters don't know which LLM they're talking to. They call invoke()
    and get back a BridgeResult. The bridge picks the provider via the
    router OR chain, manages history, and returns a normalized result.

    For claude_code: session_id is stored in SessionStore and passed
    back to the CLI to resume context between calls.

    For all other providers: message history is accumulated in SessionStore
    and sent with each request (stateless API pattern).

    If a ProviderChain is provided, it replaces the Router and enables
    automatic failover: on error, tries the next provider in priority order.
    """

    def __init__(
        self,
        router: Optional[Router] = None,
        chain: Optional[ProviderChain] = None,
        sessions: Optional[SessionStore] = None,
        system_prompt: str = "",
        quota_manager=None,
        security_policy: Optional[SecurityPolicy] = None,
        pool_router: Optional["PoolRouter"] = None,
        pool_manager: Optional["PoolManager"] = None,
    ):
        self.router = router
        self.chain = chain
        self.sessions = sessions or SessionStore()
        self.system_prompt = system_prompt
        self.quota_manager = quota_manager
        self.security_gate = AuthorizationGate(security_policy or SecurityPolicy())
        self.pool_router = pool_router
        self.pool_manager = pool_manager
        # In-memory message history per session key, for non-claude_code providers
        self._history: dict[str, list[Message]] = {}
        self._last_provider_used: dict[str, str] = {}  # session_key → provider_type

    def check_authorization(
        self,
        user_id: str,
        channel_id: str,
        action: str = "chat",
        current_scope: str = "write",
    ):
        """
        Pre-flight authorization check before invoke.

        Adapters can call this before invoking to validate permissions.
        If denied, raises AuthorizationError with reason.

        Args:
            user_id: User identifier
            channel_id: Channel identifier
            action: Requested action (default: "chat")
            current_scope: User's scope level (default: "write")

        Raises:
            AuthorizationError if not allowed
        """
        from .security import AuthorizationError

        result = self.security_gate.check_before_invoke(
            user_id, channel_id, action, current_scope
        )
        if not result.allowed:
            raise AuthorizationError(result.reason)

    async def invoke(
        self,
        prompt: str,
        session_key: str,
        tier: Optional[str] = None,
        task_type: Optional[str] = None,
        on_output: Optional[Callable[[str], None]] = None,
        on_provider_change: Optional[Callable] = None,
        system_prompt: Optional[str] = None,
        ephemeral: bool = False,
        triage: Optional["TriageResult"] = None,
    ) -> BridgeResult:
        """
        Send a prompt and return the response with automatic failover.

        Uses ProviderChain if configured, otherwise falls back to Router.
        If ProviderChain: automatically tries next provider on failure.
        If Router: single provider, error on failure (legacy mode).

        Args:
            prompt: The user message text (may include platform prefix)
            session_key: Unique key for this conversation (e.g. "mm_channel123")
            tier: Force a tier (nano/standard/deep). If None, uses routing config.
            task_type: Force a task type for routing (code/privacy/etc.)
            on_output: Optional callback when output starts arriving (for status updates)
            on_provider_change: Optional async callback(prefix, model_display, effort)
                                called when the active provider changes during failover.
                                Used by HeartbeatManager for live status updates.
            system_prompt: Override the default system prompt (used by orchestrator
                           for specialist and synthesis calls)
            ephemeral: If True, don't accumulate message history for this call.
                       Used for one-shot specialist queries.

        Returns:
            BridgeResult with response text and metadata (includes provider_type)
        """
        start = time.time()
        effective_system = system_prompt or self.system_prompt

        # Pool routing takes priority when configured and triage data is present
        if self.pool_router and triage:
            result = await self._invoke_with_pool(
                prompt, session_key, on_output, effective_system,
                ephemeral, on_provider_change, triage
            )
        elif self.chain:
            result = await self._invoke_with_chain(
                prompt, session_key, tier, on_output, effective_system,
                ephemeral, on_provider_change
            )
        else:
            result = await self._invoke_with_router(
                prompt, session_key, tier, task_type, on_output, effective_system, ephemeral
            )

        result.elapsed = time.time() - start
        self._last_provider_used[session_key] = result.provider_type
        return result

    async def _invoke_with_pool(
        self,
        prompt: str,
        session_key: str,
        on_output: Optional[Callable],
        system_prompt: str,
        ephemeral: bool,
        on_provider_change: Optional[Callable],
        triage: "TriageResult",
    ) -> BridgeResult:
        """
        Pool-based invocation with cost-class priority and automatic failover.

        Works through the pool's ordered provider list (local → free → paid).
        On rate-limit error, marks the provider, moves to next in pool.
        Records token usage against each successful provider's rate window.
        """
        pool_order = self.pool_router.select(triage)

        if not pool_order:
            # Pool router couldn't find any candidates — fall through to chain/router
            logger.warning("Pool router returned empty list — falling back to chain/router")
            if self.chain:
                return await self._invoke_with_chain(
                    prompt, session_key, None, on_output, system_prompt, ephemeral, on_provider_change
                )
            return await self._invoke_with_router(
                prompt, session_key, None, None, on_output, system_prompt, ephemeral
            )

        last_error = None
        for provider_name in pool_order:
            provider = self.pool_router.providers.get(provider_name)
            if not provider:
                continue

            # Check rate state before attempting
            if self.pool_manager and not self.pool_manager.is_available(provider_name):
                logger.debug(f"Pool skip: {provider_name} not available (rate limit)")
                continue

            if on_provider_change:
                display = getattr(provider, "config", {}).get("display_prefix", provider_name)
                model = getattr(provider, "model", "")
                await on_provider_change(display, model, None)

            provider_type = type(provider).__name__.lower().replace("provider", "")
            try:
                if provider_type == "claudecode":
                    result = await self._invoke_claude_code(
                        provider, prompt, session_key, on_output, system_prompt
                    )
                else:
                    result = await self._invoke_api_provider(
                        provider, prompt, session_key, provider_type,
                        system_prompt, ephemeral
                    )

                result.provider_type = provider_type

                # Record successful request against rate windows
                if self.pool_manager:
                    tokens = result.input_tokens + result.output_tokens
                    self.pool_manager.record_request(provider_name, tokens)

                logger.debug(
                    f"Pool invoke: {provider_name} "
                    f"({result.input_tokens}in/{result.output_tokens}out tokens)"
                )
                return result

            except Exception as e:
                err_str = str(e).lower()
                # Detect rate limit responses (429, "rate limit", "too many requests")
                if any(tok in err_str for tok in ("429", "rate limit", "too many", "quota")):
                    cooldown = 60.0
                    if self.pool_manager:
                        self.pool_manager.set_cooldown(provider_name, cooldown)
                    logger.info(
                        f"Pool: {provider_name} rate-limited — cooldown {cooldown:.0f}s, "
                        f"trying next in pool"
                    )
                else:
                    logger.warning(f"Pool: {provider_name} failed ({e}), trying next")
                last_error = e
                continue

        # All providers exhausted
        logger.error(f"Pool: all providers exhausted. Last error: {last_error}")
        return BridgeResult(
            text=f"_(All providers in pool exhausted: {last_error})_",
            provider_type="pool_exhausted",
        )

    async def _invoke_with_chain(
        self,
        prompt: str,
        session_key: str,
        tier: Optional[str],
        on_output: Optional[Callable],
        system_prompt: str,
        ephemeral: bool,
        on_provider_change: Optional[Callable] = None,
    ) -> BridgeResult:
        """Invoke using ProviderChain with automatic failover."""
        async def try_provider(provider: BaseProvider) -> BridgeResult:
            provider_type = type(provider).__name__.lower().replace("provider", "")

            try:
                if provider_type == "claudecode":
                    return await self._invoke_claude_code(
                        provider, prompt, session_key, on_output, system_prompt
                    )
                else:
                    return await self._invoke_api_provider(
                        provider, prompt, session_key, provider_type,
                        system_prompt, ephemeral
                    )
            except Exception as e:
                logger.warning(f"Provider {provider_type} failed: {e}")
                raise

        async def _on_attempt(entry: ProviderChainEntry) -> None:
            if on_provider_change:
                effort = None  # populated by caller if applicable
                await on_provider_change(
                    entry.display_prefix or entry.name,
                    entry.model_display or entry.tier,
                    effort,
                )

        success, result, provider, _fallback, error = await self.chain.try_with_fallback(
            try_provider, tier=tier, on_attempt=_on_attempt
        )

        if success:
            return result
        else:
            provider_type = "unknown"
            if provider:
                provider_type = type(provider).__name__.lower().replace("provider", "")
            logger.error(f"All providers exhausted. Last error: {error}")
            return BridgeResult(
                text=f"_(All providers failed: {error})_",
                provider_type=provider_type,
            )

    async def _invoke_with_router(
        self,
        prompt: str,
        session_key: str,
        tier: Optional[str],
        task_type: Optional[str],
        on_output: Optional[Callable],
        system_prompt: str,
        ephemeral: bool,
    ) -> BridgeResult:
        """Invoke using Router (single provider, legacy mode)."""
        provider = self.router.route(prompt, task_type=task_type, tier=tier)
        provider_type = type(provider).__name__.lower().replace("provider", "")

        try:
            if provider_type == "claudecode":
                result = await self._invoke_claude_code(
                    provider, prompt, session_key, on_output, system_prompt
                )
            else:
                result = await self._invoke_api_provider(
                    provider, prompt, session_key, provider_type,
                    system_prompt, ephemeral
                )
        except Exception as e:
            logger.error(f"Bridge invoke error ({provider_type}): {e}")
            result = BridgeResult(text=f"_(error: {e})_", provider_type=provider_type)

        result.provider_type = provider_type
        return result

    async def _invoke_claude_code(
        self, provider, prompt: str, session_key: str, on_output,
        system: str = "",
    ) -> BridgeResult:
        """Claude Code CLI — session_id-based resumption, MCP tools."""
        cc_key = session_key + "__cc"  # separate namespace from generic sessions
        session_id = self.sessions.get(cc_key)

        if session_id:
            provider.config["resume_session"] = session_id

        if on_output:
            provider.config["on_output"] = on_output

        # Normalize system prompt for deterministic cache keys
        normalized_system = normalize_for_cache(system) if system else system

        response: ProviderResponse = await provider.send(
            [Message(role="user", content=prompt)],
            system=normalized_system,
        )

        new_session_id = None
        if response.raw and isinstance(response.raw, list):
            for item in response.raw:
                if isinstance(item, dict) and item.get("session_id"):
                    new_session_id = item["session_id"]
                    break

        if new_session_id:
            await self.sessions.set(cc_key, new_session_id)

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

    async def _invoke_api_provider(
        self, provider, prompt: str, session_key: str, provider_type: str,
        system: str = "", ephemeral: bool = False,
        timeout: Optional[float] = None,
    ) -> BridgeResult:
        """All API providers — explicit message history management."""
        if ephemeral:
            messages = [Message(role="user", content=prompt)]
        else:
            history = self._history.setdefault(session_key, [])
            history.append(Message(role="user", content=prompt))

            if len(history) > MAX_HISTORY:
                self._history[session_key] = history[-MAX_HISTORY:]
                history = self._history[session_key]

            messages = history

        normalized_system = normalize_for_cache(system) if system else system

        partial_chunks: list[str] = []

        def _capture_partial(chunk: str) -> None:
            partial_chunks.append(chunk)

        try:
            send_kwargs: dict = {"system": normalized_system}
            if hasattr(provider, "config"):
                provider.config["on_chunk"] = _capture_partial

            coro = provider.send(messages, **send_kwargs)
            if timeout:
                response: ProviderResponse = await asyncio.wait_for(coro, timeout=timeout)
            else:
                response: ProviderResponse = await coro

        except asyncio.TimeoutError:
            partial = "".join(partial_chunks)
            if partial:
                recovered = partial[-500:].strip()
                logger.warning(
                    f"Provider {provider_type} timed out — recovered {len(partial)} chars of partial output"
                )
                text = recovered + " _(response truncated — provider timed out)_"
            else:
                logger.warning(f"Provider {provider_type} timed out with no partial output")
                text = "_(Provider timed out with no output)_"

            return BridgeResult(text=text, provider_type=provider_type)

        if response.content and not ephemeral:
            history = self._history.get(session_key, [])
            history.append(Message(role="assistant", content=response.content))

        usage = response.usage or {}
        cost = self._estimate_cost_generic(
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
        )

        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)

        if self.quota_manager:
            self.quota_manager.record(provider_type, inp, out, cost)

        return BridgeResult(
            text=response.content,
            cost_usd=cost,
            input_tokens=inp,
            output_tokens=out,
        )

    def clear_session(self, session_key: str) -> None:
        """Clear history and session ID for a session key."""
        self._history.pop(session_key, None)
        self._last_provider_used.pop(session_key, None)

    def get_history_length(self, session_key: str) -> int:
        return len(self._history.get(session_key, []))

    async def start_health_monitoring(self) -> None:
        """Start background health monitoring if ProviderChain is configured."""
        if self.chain:
            await self.chain.start_health_monitoring()

    async def stop_health_monitoring(self) -> None:
        """Stop background health monitoring."""
        if self.chain:
            await self.chain.stop_health_monitoring()

    async def get_provider_status(self) -> dict:
        """Get current health status of all providers in the chain."""
        if self.chain:
            return await self.chain.get_status()
        return {}

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
