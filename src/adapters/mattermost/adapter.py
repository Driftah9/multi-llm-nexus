"""
Mattermost adapter — WebSocket monitoring + REST API messaging.

Refactored from claude-brain for provider-agnostic operation.
Uses NexusBridge (not the Claude Code bridge) so any configured
provider handles the conversation.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

import aiohttp

from .api import MattermostAPI
from ...core.behaviors import NexusBehavior, tier_label
from ...core.bridge import NexusBridge
from ...core.commands import CommandRegistry
from ...core.debounce import InboundDebouncer
from ...core.formatter import PlatformFormatter
from ...core.heartbeat import HeartbeatManager, HeartbeatState
from ...core.session import SessionStore
from ...core.thread_policy import ThreadBindingPolicy, ThreadPolicy

logger = logging.getLogger(__name__)


class MattermostAdapter:
    """
    Full Mattermost adapter.

    Handles:
      - WebSocket event stream for real-time messages
      - Typing indicators + heartbeat status updates
      - Command dispatch (tier locks, session management, costs)
      - Auto-join user channels
      - Message splitting at platform limits
    """

    def __init__(self, config: dict, bridge: NexusBridge, sessions: SessionStore,
                 behavior: NexusBehavior, triage_validator=None, summary_store=None,
                 triage_provider=None):
        self.config = config
        self.bridge = bridge
        self.sessions = sessions
        self.behavior = behavior
        self.triage_validator = triage_validator
        self.summary_store = summary_store
        self.triage_provider = triage_provider

        mm = config.get("mattermost", config)
        self.token = mm.get("token", "")
        self.server_url = mm.get("url", "http://localhost:8065/api/v4")
        self.team_name = mm.get("team", "main")
        self.bot_name = config.get("bot_name", "Nexus")
        self.allowed_user: Optional[str] = mm.get("allowed_user")
        self.channel_map: dict = config.get("channel_map", {})
        self.projects_dir = Path(config.get("projects_dir", "/home/claude/projects"))

        self.api = MattermostAPI(self.server_url, self.token)
        self.commands = CommandRegistry("mattermost")
        self.fmt = PlatformFormatter("mattermost")
        self.debouncer = InboundDebouncer(
            window_ms=mm.get("debounce_window_ms", 500)
        )
        self.thread_policy = ThreadBindingPolicy()
        # Load channel-specific thread policies from config
        for ch_id, ch_config in config.get("channel_thread_policies", {}).items():
            policy = ThreadPolicy(
                mode=ch_config.get("mode", "isolated"),
                session_prefix=ch_config.get("session_prefix", "mattermost"),
                include_user_in_key=ch_config.get("include_user_in_key", False),
            )
            self.thread_policy.set_channel_policy(ch_id, policy)

        self._bot_user_id = ""
        self._team_id = ""
        self._channel_cache: dict[str, str] = {}
        self._user_cache: dict[str, str] = {}
        self._costs: dict[str, dict] = {}
        self._stop = asyncio.Event()

    async def run(self) -> None:
        await self.api.start()
        try:
            bot = await self.api.get_bot_user()
            self._bot_user_id = bot["id"]
            logger.info(f"Mattermost: {bot['username']} ({self._bot_user_id})")

            team = await self.api.get_team_by_name(self.team_name)
            self._team_id = team["id"]

            try:
                ts = await self.api.get(f"/teams/{self._team_id}/channels/name/town-square")
                await self.api.post_message(ts["id"], f"{self.bot_name} is online.")
            except Exception:
                pass

            asyncio.create_task(self._channel_sync_loop())
            await self._ws_loop()
        finally:
            await self.api.stop()

    async def stop(self) -> None:
        self._stop.set()

    # ── WebSocket loop ────────────────────────────────────────────────────

    async def _ws_loop(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.ws_connect(
                        self.api.ws_url,
                        heartbeat=30,
                        headers={"Authorization": f"Bearer {self.token}"},
                    ) as ws:
                        backoff = 2
                        logger.info("Mattermost WebSocket connected")
                        await ws.send_str(json.dumps({
                            "seq": 1, "action": "authentication_challenge",
                            "data": {"token": self.token},
                        }))
                        async for msg in ws:
                            if self._stop.is_set():
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                event = json.loads(msg.data)
                                if event.get("event") == "posted":
                                    asyncio.create_task(self._handle_post(event))
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as e:
                logger.warning(f"WebSocket error: {e} — reconnecting in {backoff}s")

            if not self._stop.is_set():
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    # ── Message handling ──────────────────────────────────────────────────

    async def _handle_post(self, event: dict) -> None:
        post = json.loads(event.get("data", {}).get("post", "{}"))
        if post.get("type"):
            return
        user_id = post.get("user_id", "")
        if user_id == self._bot_user_id:
            return

        channel_id = post.get("channel_id", "")
        message = post.get("message", "").strip()
        post_id = post.get("id", "")
        root_id = post.get("root_id", "") or post_id

        if self.allowed_user:
            username = await self._resolve_username(user_id)
            if username != self.allowed_user:
                return

        if not message:
            return

        if self.debouncer.should_skip(user_id, channel_id, message):
            logger.debug(f"Debounced rapid message from {user_id} in {channel_id}")
            return

        channel_name = await self._resolve_channel(channel_id)
        # Generate session key respecting thread binding policy
        thread_id = root_id if root_id != post_id else None
        session_key = self.thread_policy.get_session_key(
            f"mm_{channel_id}", thread_id=thread_id, user_id=user_id
        )

        if self.commands.is_command(message):
            await self._handle_command(message, channel_id, post_id, session_key, channel_name)
            return

        await self._handle_message(message, channel_id, post_id, root_id, session_key, channel_name)

    async def _handle_message(self, message: str, channel_id: str, post_id: str,
                               root_id: str, session_key: str, channel_name: str) -> None:
        await self.sessions.mark_active(session_key)

        # Triage to determine tier + effort
        triage = await self.behavior.route_message(message, channel_name, "mattermost")
        tier_display = tier_label(triage.tier)
        invoke_start = __import__("time").time()

        # Record triage decision for feedback loop
        decision_id = -1
        if self.triage_validator and triage.source == "triage":
            decision_id = self.triage_validator.record_decision(
                channel=channel_name,
                message_hash=self.triage_validator.hash_message(message),
                classified_tier=triage.tier,
                classified_effort=triage.effort,
            )

        reply_to = root_id if root_id != post_id else ""
        placeholder = await self.api.post_message(
            channel_id,
            f"_thinking ({tier_display})..._",
            reply_to,
        )
        placeholder_id = placeholder.get("id", "")

        async def _push_heartbeat(post_id: str, text: str) -> None:
            try:
                await self.api.update_message(post_id, text)
            except Exception:
                pass

        hb_state = HeartbeatState(
            post_id=placeholder_id,
            display_prefix=self.bot_name,
            model_display=tier_display,
            effort=None,
        )
        heartbeat = HeartbeatManager(hb_state, _push_heartbeat).start()
        typing_task = asyncio.create_task(self._typing_loop(channel_id))

        # Inject prior session context if available
        session_context = ""
        if self.summary_store:
            session_context = self.summary_store.inject_context(session_key)

        prompt = f"[Platform: Mattermost | Channel: #{channel_name}]\n{message}"
        if session_context:
            prompt = session_context + prompt

        orchestrator = getattr(getattr(self, "engine", None), "orchestrator", None)
        use_orchestrator = (
            orchestrator is not None and orchestrator.should_orchestrate(channel_name)
        )

        try:
            if use_orchestrator:
                orch_result = await orchestrator.dispatch(
                    message=message,
                    context=channel_name,
                    session_key=session_key,
                    operator_context=f"Platform: Mattermost | Channel: #{channel_name}",
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
                )
                response_text = bridge_result.text
                cost_usd = bridge_result.cost_usd
        finally:
            heartbeat.stop()
            typing_task.cancel()

        if cost_usd > 0:
            c = self._costs.setdefault(session_key, {"cost_usd": 0.0, "responses": 0})
            c["cost_usd"] += cost_usd
            c["responses"] += 1

        # Record response in triage validator
        if self.triage_validator and decision_id > 0:
            elapsed_ms = (__import__("time").time() - invoke_start) * 1000
            self.triage_validator.record_response(
                decision_id=decision_id,
                response_length=len(response_text or ""),
                response_ms=elapsed_ms,
                channel=channel_name,
            )

        # ReviewGate: check if this looks like a commit/change-heavy response
        review_hint = self._check_review_gate(message, response_text or "")

        response = response_text or "_(no response)_"
        if review_hint:
            response = response + f"\n\n---\n{review_hint}"
        chunks = self.fmt.format_response(response)

        if placeholder_id:
            await self.api.update_message(placeholder_id, chunks[0])
        else:
            await self.api.post_message(channel_id, chunks[0], reply_to)
        for chunk in chunks[1:]:
            await self.api.post_message(channel_id, chunk, reply_to)

    # ── Command handling ──────────────────────────────────────────────────

    async def _handle_command(self, message: str, channel_id: str, post_id: str,
                               session_key: str, channel_name: str) -> None:
        cmd, args = self.commands.parse(message)
        if not cmd:
            await self.api.post_message(channel_id, f"Unknown command. Try `!help`.")
            return

        # Behavioral commands — NexusBehavior handles them
        if cmd.behavioral:
            prev_tier = self.behavior.prefs.tier_override
            event = self.behavior.handle_command(message, channel_name, "mattermost")
            if event:
                # Record tier override signal for triage validator
                if self.triage_validator and event.event_type.value == "tier_changed":
                    new_tier = self.behavior.prefs.tier_override or "standard"
                    if prev_tier != new_tier and not prev_tier:
                        # Was on auto-triage, now locking — record as override
                        self.triage_validator.record_override(
                            channel_name, from_tier="standard", to_tier=new_tier
                        )
                elif self.triage_validator and event.event_type.value == "auto_enabled":
                    self.triage_validator.record_auto_released(channel_name)
                await self.api.post_message(channel_id, f"_{event.detail}_")
            return

        # Platform commands — handled here
        name = cmd.name

        if name in ("new", "reset"):
            # Distill current session before clearing (if summary store available)
            if self.summary_store and self.triage_provider:
                history = self.bridge._history.get(session_key, [])
                if history:
                    asyncio.create_task(
                        self.summary_store.distill(session_key, history, self.triage_provider)
                    )
            # Record quick reset signal for triage validator
            if self.triage_validator:
                self.triage_validator.record_reset(channel_name)
            self.bridge.clear_session(session_key)
            await self.sessions.clear(session_key)
            await self.api.post_message(channel_id, "Session cleared. Starting fresh.")

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
            await self.api.post_message(channel_id, text)

        elif name == "providers":
            lines = ["**Configured providers:**"]
            for name_, p in self.bridge.router.providers.items():
                lines.append(f"- `{name_}` — {repr(p)}")
            await self.api.post_message(channel_id, "\n".join(lines))

        elif name == "costs":
            if not self._costs:
                await self.api.post_message(channel_id, "No cost data yet.")
            else:
                lines = ["**Costs:**\n"]
                total = 0.0
                for k, d in sorted(self._costs.items(), key=lambda x: x[1]["cost_usd"], reverse=True):
                    cid = k.replace("mm_", "")
                    ch = self._channel_cache.get(cid, cid)
                    lines.append(f"- **#{ch}:** ${d['cost_usd']:.4f} ({d['responses']} msgs)")
                    total += d["cost_usd"]
                lines.append(f"\n**Total: ${total:.4f}**")
                await self.api.post_message(channel_id, "\n".join(lines))

        elif name == "clean":
            count = int(args) if args.isdigit() else None
            delete_all = args.lower() == "all"
            pinned = await self.api.get_pinned_posts(channel_id)
            posts_data = await self.api.get_channel_posts(channel_id)
            order = posts_data.get("order", [])
            to_delete = [p for p in order if p != post_id and p not in pinned]
            if not delete_all and count:
                to_delete = to_delete[:count]
            deleted = 0
            for pid in to_delete:
                try:
                    await self.api.delete_post(pid)
                    deleted += 1
                except Exception:
                    pass
            status_msg = await self.api.post_message(channel_id, f"Cleaned {deleted} message(s).")
            await asyncio.sleep(3)
            for pid in [post_id, status_msg.get("id", "")]:
                try:
                    await self.api.delete_post(pid)
                except Exception:
                    pass

        elif name == "spaces":
            space_registry = getattr(getattr(self, "engine", None), "space_registry", None)
            if space_registry:
                summary = space_registry.summary()
                await self.api.post_message(channel_id, f"**Registered Spaces:**\n{summary}")
            else:
                await self.api.post_message(channel_id, "No space registry loaded.")

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
                    await self.api.post_message(channel_id, "\n".join(lines))
                else:
                    await self.api.post_message(channel_id, "No specialist workspace mapped to this channel.")
            else:
                await self.api.post_message(channel_id, "Orchestrator not enabled.")

        elif name == "help":
            await self.api.post_message(channel_id, self.commands.help_text())

    # ── Support loops ─────────────────────────────────────────────────────

    async def _typing_loop(self, channel_id: str) -> None:
        try:
            while True:
                await self.api.set_typing(channel_id)
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    async def _channel_sync_loop(self) -> None:
        await asyncio.sleep(10)
        while not self._stop.is_set():
            try:
                if self.allowed_user:
                    user = await self.api.get(f"/users/username/{self.allowed_user}")
                    user_channels = await self.api.get_user_channels(user["id"], self._team_id)
                    bot_channels = await self.api.get_user_channels(self._bot_user_id, self._team_id)
                    bot_ids = {c["id"] for c in bot_channels}
                    for ch in user_channels:
                        if ch["id"] not in bot_ids and ch.get("type") != "D":
                            try:
                                await self.api.add_user_to_channel(ch["id"], self._bot_user_id)
                                logger.info(f"Auto-joined #{ch.get('name')}")
                            except Exception:
                                pass
            except Exception as e:
                logger.debug(f"Channel sync: {e}")
            await asyncio.sleep(60)

    # ── Helpers ───────────────────────────────────────────────────────────

    async def _resolve_channel(self, channel_id: str) -> str:
        if channel_id in self._channel_cache:
            return self._channel_cache[channel_id]
        try:
            ch = await self.api.get_channel(channel_id)
            name = ch.get("name", channel_id)
            self._channel_cache[channel_id] = name
            return name
        except Exception:
            return channel_id

    async def _resolve_username(self, user_id: str) -> str:
        if user_id in self._user_cache:
            return self._user_cache[user_id]
        try:
            user = await self.api.get_user(user_id)
            name = user.get("username", user_id)
            self._user_cache[user_id] = name
            return name
        except Exception:
            return user_id

    def _check_review_gate(self, message: str, response: str) -> str | None:
        """
        Estimate change scope from response content and run the ReviewGate.
        Returns a suggestion string if review is recommended, else None.
        """
        import re
        code_blocks = re.findall(r"```[\s\S]*?```", response)
        lines_added = sum(b.count("\n") for b in code_blocks)
        lines_deleted = sum(1 for line in message.splitlines() if line.strip().startswith("-"))
        files = re.findall(r"`([^`]+\.(?:py|js|ts|go|rs|yaml|yml|toml|json|sh))`", response)
        is_commit_point = any(kw in message.lower() for kw in ("commit", "push", "deploy", "merge"))

        from ...core.review_gate import ReviewTrigger
        trigger, suggestion = self.behavior.check_review_trigger(
            files=files or [],
            lines_added=lines_added,
            lines_deleted=lines_deleted,
            is_commit_point=is_commit_point,
        )
        if trigger != ReviewTrigger.NONE and suggestion:
            return f"_{suggestion}_"
        return None

    async def deliver(self, outbound) -> None:
        """Engine callback — post an autonomously-generated response to a channel."""
        try:
            chunks = self.fmt.format_response(outbound.content)
            for chunk in chunks:
                await self.api.post_message(outbound.channel_id, chunk)
        except Exception as e:
            logger.error(f"deliver() failed for channel {outbound.channel_id}: {e}")
