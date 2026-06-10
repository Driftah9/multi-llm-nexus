"""
Adapter base class — all platform connectors share this.

Handles:
  - Message triage (5-dimension classification)
  - Session management
  - Bridge invocation with pool routing
  - Command dispatch and behavior control
  - Typing indicators and heartbeat status
  - Platform-specific formatting (markdown, char limits)

Each adapter defines:
  - PLATFORM_PROPS = {max_chars, markdown_support, ...}
  - run() — connect and listen for messages
  - send() — post a message back to platform
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional, Callable, TypeVar, TYPE_CHECKING

# Deterministic liveness pings ("are you there?", "you here?"): short, standalone
# connectivity checks. Matched whole-message (anchored) so real questions like
# "are you there to help with X" are NOT swallowed. Answered with a canned reply and
# NO LLM call — instant and immune to confabulation. Same shortcut as claude-brain.
_LIVENESS_RE = re.compile(
    r'^\s*(?:'
    r'are\s+you\s+(?:there|here|up|alive|online|awake|around|listening)'
    r'|you\s+(?:there|here|up|alive|around|listening)'
    r'|still\s+(?:there|here|with\s+me|alive|awake)'
    r'|(?:are\s+you\s+)?on(?:line)?'
    r')\s*[\?\.!]*\s*$',
    re.IGNORECASE,
)

from .api_base import APISenderBase
from ..core.behaviors import NexusBehavior, tier_label
from ..core.bridge import NexusBridge
from ..core.commands import CommandRegistry
from ..core.debounce import InboundDebouncer
from ..core.formatter import PlatformFormatter
from ..core.heartbeat import HeartbeatManager, HeartbeatState
from ..core.session import SessionStore
from ..core.triage import Triage
from ..core.user_gate import UserGate

logger = logging.getLogger(__name__)

T = TypeVar("T")


class AdapterBase:
    """
    Base class for all communication protocol adapters.

    Subclasses define PLATFORM_PROPS and implement platform-specific methods.
    All intelligence (triage, routing, sessions, commands) is inherited.
    """

    # Override in subclasses
    PLATFORM_PROPS = {
        "max_chars": None,           # None = unlimited
        "markdown_support": "full",  # full | partial | none
        "platform_name": "unknown",
        "debounce_ms": 500,
    }

    def __init__(
        self,
        config: dict,
        bridge: NexusBridge,
        sessions: SessionStore,
        behavior: NexusBehavior,
        triage_validator=None,
        summary_store=None,
        triage_provider=None,
    ):
        self.config = config
        self.bridge = bridge
        self.sessions = sessions
        self.behavior = behavior
        self.triage_validator = triage_validator
        self.summary_store = summary_store
        self.triage_provider = triage_provider

        # Common components
        self._triage = Triage(provider=triage_provider)
        platform = self.PLATFORM_PROPS.get("platform_name", "unknown")
        self.commands = CommandRegistry(platform)
        self.fmt = PlatformFormatter(platform)
        self.debouncer = InboundDebouncer(
            window_ms=self.PLATFORM_PROPS.get("debounce_ms", 500)
        )

        # User gate — pre-triage authorization (zero cloud cost)
        # Builds from platform-specific config block if present
        platform_cfg = config.get(platform, config)
        self._gate = UserGate(
            platform=platform,
            config=platform_cfg,
            local_provider=triage_provider,  # nano provider reused for intent checks
        )

        self._stop = asyncio.Event()
        self._costs: dict[str, dict] = {}
        self.engine = None  # Set by main.py after instantiation

    # ── Platform-specific overrides (required) ─────────────────────────────

    async def run(self) -> None:
        """Connect to platform and listen for messages. Override in subclass."""
        raise NotImplementedError

    async def stop(self) -> None:
        """Gracefully disconnect. Default stops event; override if more needed."""
        self._stop.set()

    async def send(self, channel_id: str, text: str, reply_to: Optional[str] = None) -> None:
        """
        Post a message to the platform.

        Args:
            channel_id: Platform-specific channel/chat identifier
            text: Message text (already formatted by caller)
            reply_to: Optional ID for threading/replies
        """
        raise NotImplementedError

    # ── Common message handling pipeline ──────────────────────────────────

    async def handle_incoming(
        self,
        text: str,
        channel_id: str,
        channel_name: str,
        user_id: Optional[str] = None,
        post_id: Optional[str] = None,
        root_id: Optional[str] = None,
        session_key: Optional[str] = None,
    ) -> None:
        """
        Process an incoming message: triage, session, invoke, format response.

        Args:
            text: Raw message content
            channel_id: Platform channel ID
            channel_name: Human-readable channel name
            user_id: User who sent the message (for debounce/allowlist)
            post_id: Unique message ID on platform
            root_id: Thread root ID (for threading)
            session_key: Pre-computed session key (if None, use channel_id)
        """
        text = text.strip()
        if not text:
            return

        # ── Pre-triage gate (zero cloud cost) ─────────────────────────────
        # Checks user ACL, rate limits, hard deny patterns, local LLM intent.
        # Operators (scope: all) pass instantly. Unknown users denied by default.
        # Silent drop — no response sent, no tokens spent.
        if user_id:
            gate = await self._gate.check(user_id=user_id, message=text)
            if not gate.allowed:
                logger.debug(
                    f"Gate drop: user={user_id} platform={self.PLATFORM_PROPS['platform_name']} "
                    f"reason={gate.reason}"
                )
                return

        session_key = session_key or f"{self.PLATFORM_PROPS['platform_name']}_{channel_id}"
        await self.sessions.mark_active(session_key)

        # Check for commands
        if self.commands.is_command(text):
            await self._handle_command(text, channel_id, channel_name, session_key, post_id)
            return

        reply_to = root_id if root_id and root_id != post_id else ""

        # Deterministic liveness shortcut: "are you there?" → "Yes." with NO LLM call
        # (no triage, no provider, no confabulation). Reached only after the gate, so
        # owner/allowed users only.
        if _LIVENESS_RE.match(text.strip()):
            await self.send(channel_id, "Yes.", reply_to)
            logger.info("Liveness ping → 'Yes.' (no LLM)")
            return

        # Instant acknowledgment: post the placeholder NOW, BEFORE triage, so the user
        # sees feedback immediately instead of waiting through classification. The
        # heartbeat below refines it with the resolved provider during invoke.
        placeholder_id = await self._send_placeholder(channel_id, "…", reply_to)

        # Single classification on the hot path: run the 5-dimension triage ONCE, then
        # derive the tier/effort routing from it (precomputed_tier) instead of firing a
        # SECOND nano LLM call — the double-triage collapse.
        triage_result = await self._triage.classify(text)
        triage = await self.behavior.route_message(
            text, channel_name, self.PLATFORM_PROPS["platform_name"],
            precomputed_tier=triage_result.estimated_complexity,
        )
        tier_display = tier_label(triage.tier)

        invoke_start = __import__("time").time()

        # Record triage decision for feedback loop
        decision_id = -1
        if self.triage_validator and triage.source == "triage":
            decision_id = self.triage_validator.record_decision(
                channel=channel_name,
                message_hash=self.triage_validator.hash_message(text),
                classified_tier=triage.tier,
                classified_effort=triage.effort,
            )

        # Heartbeat for live status updates (has access to channel_id from enclosing scope)
        async def _push_heartbeat(pid: str, content: str) -> None:
            try:
                await self._update_placeholder(pid, content, channel_id)
            except Exception:
                pass

        hb_state = HeartbeatState(
            post_id=placeholder_id,
            display_prefix=self.config.get("agent_name", "Nexus"),
            model_display=tier_display,
            effort=None,
        )
        heartbeat = HeartbeatManager(hb_state, _push_heartbeat).start()

        # Session context injection
        session_context = ""
        if self.summary_store:
            session_context = self.summary_store.inject_context(session_key)

        prompt = f"[Platform: {self.PLATFORM_PROPS['platform_name']} | Channel: #{channel_name}]\n{text}"
        if session_context:
            prompt = session_context + prompt

        # Check for orchestrator dispatch
        orchestrator = getattr(getattr(self, "engine", None), "orchestrator", None)
        use_orchestrator = (
            orchestrator is not None
            and orchestrator.should_orchestrate(channel_name)
        )

        try:
            if use_orchestrator:
                orch_result = await orchestrator.dispatch(
                    message=text,
                    context=channel_name,
                    session_key=session_key,
                    operator_context=f"Platform: {self.PLATFORM_PROPS['platform_name']} | Channel: #{channel_name}",
                    heartbeat=heartbeat,
                )
                if not orch_result.bypassed:
                    response_text = orch_result.response
                    cost_usd = orch_result.total_cost
                else:
                    bridge_result = await self.bridge.invoke(
                        prompt=prompt,
                        session_key=session_key,
                        tier=triage.tier,
                        task_type=triage.provider_key,
                        on_provider_change=heartbeat.set_provider,
                        triage=triage_result,
                    )
                    response_text = bridge_result.text
                    cost_usd = bridge_result.cost_usd
            else:
                bridge_result = await self.bridge.invoke(
                    prompt=prompt,
                    session_key=session_key,
                    tier=triage.tier,
                    task_type=triage.provider_key,
                    on_provider_change=heartbeat.set_provider,
                    triage=triage_result,
                )
                response_text = bridge_result.text
                cost_usd = bridge_result.cost_usd
        finally:
            heartbeat.stop()
            await self._stop_typing(channel_id)

        if cost_usd > 0:
            c = self._costs.setdefault(session_key, {"cost_usd": 0.0, "responses": 0})
            c["cost_usd"] += cost_usd
            c["responses"] += 1

        # Record response in validator
        if self.triage_validator and decision_id > 0:
            elapsed_ms = (__import__("time").time() - invoke_start) * 1000
            self.triage_validator.record_response(
                decision_id=decision_id,
                response_length=len(response_text or ""),
                response_ms=elapsed_ms,
                channel=channel_name,
            )

        # ReviewGate check (if available)
        review_hint = self._check_review_gate(text, response_text or "")

        response = response_text or "_(no response)_"
        if review_hint:
            response = response + f"\n\n---\n{review_hint}"

        # Format and send response
        chunks = self.fmt.format_response(response)
        if placeholder_id:
            await self._update_placeholder(placeholder_id, chunks[0], channel_id)
        else:
            await self.send(channel_id, chunks[0], reply_to)

        for chunk in chunks[1:]:
            await asyncio.sleep(0.1)
            await self.send(channel_id, chunk, reply_to)

    # ── Command handling ──────────────────────────────────────────────────

    async def _handle_command(
        self,
        text: str,
        channel_id: str,
        channel_name: str,
        session_key: str,
        post_id: Optional[str] = None,
    ) -> None:
        """Dispatch command to behavior or platform handler."""
        cmd, args = self.commands.parse(text)
        if not cmd:
            await self.send(channel_id, "Unknown command. Try `!help`.")
            return

        platform = self.PLATFORM_PROPS["platform_name"]

        # Behavioral commands
        if cmd.behavioral:
            prev_tier = self.behavior.prefs.tier_override
            event = self.behavior.handle_command(text, channel_name, platform)
            if event:
                # Record tier override
                if self.triage_validator and event.event_type.value == "tier_changed":
                    new_tier = self.behavior.prefs.tier_override or "standard"
                    if prev_tier != new_tier and not prev_tier:
                        self.triage_validator.record_override(
                            channel_name, from_tier="standard", to_tier=new_tier
                        )
                elif self.triage_validator and event.event_type.value == "auto_enabled":
                    self.triage_validator.record_auto_released(channel_name)
                await self.send(channel_id, f"_{event.detail}_")
            return

        name = cmd.name

        if name in ("new", "reset"):
            # Distill session summary
            if self.summary_store and self.triage_provider:
                history = self.bridge._history.get(session_key, [])
                if history:
                    asyncio.create_task(
                        self.summary_store.distill(session_key, history, self.triage_provider)
                    )
            if self.triage_validator:
                self.triage_validator.record_reset(channel_name)
            self.bridge.clear_session(session_key)
            await self.sessions.clear(session_key)
            await self.send(channel_id, "Session cleared. Starting fresh.")

        elif name == "status":
            status = self.behavior.get_status()
            hist = self.bridge.get_history_length(session_key)
            cost = self._costs.get(session_key, {})
            text = (
                f"**Channel:** #{channel_name}\n"
                f"**Tier:** {tier_label(status.get('tier_override') or 'standard')}"
                + (" *(auto)*" if status["auto_triage"] else " *(locked)*") + "\n"
                f"**Effort:** {status.get('effort_override') or 'auto'}\n"
                f"**Provider:** {status.get('provider_override') or 'default'}\n"
                f"**History:** {hist} messages"
            )
            if cost:
                text += f"\n**Cost:** ${cost['cost_usd']:.4f} ({cost['responses']} msgs)"
            await self.send(channel_id, text)

        elif name == "providers":
            lines = ["**Configured providers:**"]
            for name_, p in self.bridge.router.providers.items():
                lines.append(f"- `{name_}` — {repr(p)}")
            await self.send(channel_id, "\n".join(lines))

        elif name == "costs":
            if not self._costs:
                await self.send(channel_id, "No cost data yet.")
            else:
                lines = ["**Costs:**\n"]
                total = 0.0
                for k, d in sorted(self._costs.items(), key=lambda x: x[1]["cost_usd"], reverse=True):
                    lines.append(f"- {k}: ${d['cost_usd']:.4f} ({d['responses']} msgs)")
                    total += d["cost_usd"]
                lines.append(f"\n**Total: ${total:.4f}**")
                await self.send(channel_id, "\n".join(lines))

        elif name == "spaces":
            space_registry = getattr(getattr(self, "engine", None), "space_registry", None)
            if space_registry:
                summary = space_registry.summary()
                await self.send(channel_id, f"**Registered Spaces:**\n{summary}")
            else:
                await self.send(channel_id, "No space registry loaded.")

        elif name == "specialists":
            orchestrator = getattr(getattr(self, "engine", None), "orchestrator", None)
            if orchestrator:
                ws = orchestrator.get_workspace(channel_name)
                if ws and ws.specialists:
                    lines = [f"**Specialists in `{ws.display_name}` workspace:**\n"]
                    for sid in ws.specialists:
                        profile = orchestrator.specialists.get(sid)
                        if profile:
                            lines.append(f"- **{profile.name}** (`{sid}`) — {profile.scope or profile.name}")
                        else:
                            lines.append(f"- `{sid}` _(profile not loaded)_")
                    await self.send(channel_id, "\n".join(lines))
                else:
                    await self.send(channel_id, "No specialist workspace mapped to this channel.")
            else:
                await self.send(channel_id, "Orchestrator not enabled.")

        elif name == "help":
            await self.send(channel_id, self.commands.help_text())

    # ── Platform-specific overrides (optional) ────────────────────────────

    async def _send_placeholder(self, channel_id: str, tier_display: str,
                               reply_to: Optional[str] = None) -> str:
        """
        Post a thinking placeholder. Override if platform needs special handling.
        Returns the placeholder message ID (for heartbeat updates).
        """
        return ""

    async def _update_placeholder(self, placeholder_id: str, text: str,
                                 channel_id: Optional[str] = None) -> None:
        """Update a thinking placeholder. Override if platform supports edits."""
        pass

    async def _stop_typing(self, channel_id: str) -> None:
        """Stop typing indicator. Override if platform supports it."""
        pass

    def _check_review_gate(self, message: str, response: str) -> str | None:
        """
        Review gate suggestion. Override if needed.
        Returns suggestion string or None.
        """
        import re
        code_blocks = re.findall(r"```[\s\S]*?```", response)
        lines_added = sum(b.count("\n") for b in code_blocks)
        files = re.findall(r"`([^`]+\.(?:py|js|ts|go|rs|yaml|yml|toml|json|sh))`", response)
        is_commit_point = any(kw in message.lower() for kw in ("commit", "push", "deploy", "merge"))

        from ..core.review_gate import ReviewTrigger
        trigger, suggestion = self.behavior.check_review_trigger(
            files=files or [],
            lines_added=lines_added,
            lines_deleted=0,
            is_commit_point=is_commit_point,
        )
        if trigger != ReviewTrigger.NONE and suggestion:
            return f"_{suggestion}_"
        return None

    async def deliver(self, outbound) -> None:
        """Engine callback — post an autonomously-generated response."""
        try:
            chunks = self.fmt.format_response(outbound.content)
            for chunk in chunks:
                await self.send(outbound.channel_id, chunk)
        except Exception as e:
            logger.error(f"deliver() failed for channel {outbound.channel_id}: {e}")
