"""
Mattermost adapter — WebSocket monitoring + REST API messaging.

Inherits all message handling, triage, and command logic from AdapterBase.
Only implements MM-specific: connection, send, thread policies, auto-join.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

import aiohttp

from .api import MattermostAPI
from ..adapter_base import AdapterBase
from ...core.behaviors import NexusBehavior
from ...core.bridge import NexusBridge
from ...core.session import SessionStore
from ...core.thread_policy import ThreadBindingPolicy, ThreadPolicy

logger = logging.getLogger(__name__)


class MattermostAdapter(AdapterBase):
    """
    Mattermost adapter.

    Inherits from AdapterBase:
      - Triage (5-dimension message classification)
      - Session management
      - Command dispatch (tier locks, costs, etc.)
      - Bridge invocation with pool routing

    Implements Mattermost-specific:
      - WebSocket event stream
      - Thread binding policies
      - Auto-join channels
      - Typing indicators
    """

    PLATFORM_PROPS = {
        "platform_name": "mattermost",
        "max_chars": None,  # Mattermost allows very long messages
        "markdown_support": "full",
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
        super().__init__(
            config,
            bridge,
            sessions,
            behavior,
            triage_validator,
            summary_store,
            triage_provider,
        )

        mm = config.get("mattermost", config)
        self.token = mm.get("token", "")
        self.server_url = mm.get("url", "http://localhost:8065/api/v4")
        self.team_name = mm.get("team", "main")
        self.bot_name = config.get("bot_name", "Nexus")
        self.allowed_user: Optional[str] = mm.get("allowed_user")
        self.channel_map: dict = config.get("channel_map", {})
        self.workdir = Path(config.get("workdir", str(Path.home()))).expanduser()

        self.api = MattermostAPI(self.server_url, self.token)
        self.thread_policy = ThreadBindingPolicy()
        # Load channel-specific thread policies
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
        self._typing_tasks: dict[str, asyncio.Task] = {}

    async def run(self) -> None:
        """Connect and listen for WebSocket events."""
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

    async def _ws_loop(self) -> None:
        """WebSocket event loop with reconnection backoff."""
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
                        await ws.send_str(
                            json.dumps({
                                "seq": 1,
                                "action": "authentication_challenge",
                                "data": {"token": self.token},
                            })
                        )
                        async for msg in ws:
                            if self._stop.is_set():
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                event = json.loads(msg.data)
                                if event.get("event") == "posted":
                                    asyncio.create_task(self._on_posted(event))
                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break
            except Exception as e:
                logger.warning(f"WebSocket error: {e} — reconnecting in {backoff}s")

            if not self._stop.is_set():
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _on_posted(self, event: dict) -> None:
        """Handle posted message event from WebSocket."""
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
        thread_id = root_id if root_id != post_id else None
        session_key = self.thread_policy.get_session_key(
            f"mm_{channel_id}", thread_id=thread_id, user_id=user_id
        )

        # Delegate to base class message handler
        await self.handle_incoming(
            text=message,
            channel_id=channel_id,
            channel_name=channel_name,
            user_id=user_id,
            post_id=post_id,
            root_id=root_id,
            session_key=session_key,
        )

    # ── Platform overrides (from base) ────────────────────────────────────

    async def send(self, channel_id: str, text: str, reply_to: Optional[str] = None) -> None:
        """Post a message to Mattermost."""
        try:
            await self.api.post_message(channel_id, text, reply_to)
        except Exception as e:
            logger.error(f"Failed to send to {channel_id}: {e}")

    async def _send_placeholder(
        self, channel_id: str, tier_display: str, reply_to: Optional[str] = None
    ) -> str:
        """Post thinking placeholder."""
        try:
            msg = await self.api.post_message(
                channel_id,
                f"_thinking ({tier_display})..._",
                reply_to,
            )
            return msg.get("id", "")
        except Exception:
            return ""

    async def _update_placeholder(self, placeholder_id: str, text: str,
                                 channel_id: Optional[str] = None) -> None:
        """Update thinking placeholder with response."""
        try:
            await self.api.update_message(placeholder_id, text)
        except Exception:
            pass

    async def _stop_typing(self, channel_id: str) -> None:
        """Cancel typing indicator task if running."""
        task = self._typing_tasks.pop(channel_id, None)
        if task:
            task.cancel()

    # ── Mattermost-specific helpers ───────────────────────────────────────

    async def _resolve_channel(self, channel_id: str) -> str:
        """Get channel name from ID with caching."""
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
        """Get username from ID with caching."""
        if user_id in self._user_cache:
            return self._user_cache[user_id]
        try:
            user = await self.api.get_user(user_id)
            name = user.get("username", user_id)
            self._user_cache[user_id] = name
            return name
        except Exception:
            return user_id

    async def _typing_loop(self, channel_id: str) -> None:
        """Send periodic typing indicators."""
        try:
            while True:
                await self.api.set_typing(channel_id)
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    async def _channel_sync_loop(self) -> None:
        """Auto-join user channels."""
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
