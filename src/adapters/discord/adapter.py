"""
Discord adapter — REST API polling.

Inherits all message handling, triage, and command logic from AdapterBase.
Only implements Discord-specific: REST polling and message sending.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Optional, Set

from ..adapter_base import AdapterBase
from ...core.behaviors import NexusBehavior
from ...core.bridge import NexusBridge
from ...core.session import SessionStore

logger = logging.getLogger(__name__)

DISCORD_API = "https://discord.com/api/v10"


class DiscordAdapter(AdapterBase):
    """
    Discord adapter using REST polling.

    Inherits from AdapterBase:
      - Triage (5-dimension message classification)
      - Session management
      - Command dispatch
      - Bridge invocation with pool routing

    Implements Discord-specific:
      - REST polling for new messages
      - Message posting and editing
    """

    PLATFORM_PROPS = {
        "platform_name": "discord",
        "max_chars": 2000,
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

        dc = config.get("discord", config)
        self.token = dc.get("token", "")
        self.channel_id = str(dc.get("channel_id", ""))
        self.allowed_users: Set[int] = set(dc.get("allowed_users", []))
        self.poll_interval: int = dc.get("poll_interval", 10)
        self.last_message_id: Optional[str] = None

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json",
            "User-Agent": "DiscordBot (multi-llm-nexus, 1.0)",
        }

    def _get(self, path: str) -> dict:
        req = urllib.request.Request(f"{DISCORD_API}{path}", headers=self._headers())
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    def _post(self, path: str, data: dict) -> dict:
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            f"{DISCORD_API}{path}",
            data=body,
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    def _patch(self, path: str, data: dict) -> dict:
        body = json.dumps(data).encode()
        req = urllib.request.Request(
            f"{DISCORD_API}{path}",
            data=body,
            headers=self._headers(),
            method="PATCH",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    async def run(self) -> None:
        """Connect and listen for new messages."""
        if not self.token or not self.channel_id:
            logger.warning("Discord not configured — adapter disabled")
            return

        try:
            msgs = await asyncio.to_thread(
                self._get,
                f"/channels/{self.channel_id}/messages?limit=1",
            )
            self.last_message_id = msgs[0]["id"] if msgs else "0"
        except Exception as e:
            logger.error(f"Discord init failed: {e}")
            self.last_message_id = "0"

        logger.info(f"Discord adapter started (channel {self.channel_id})")

        while not self._stop.is_set():
            try:
                await self._poll()
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    retry = float(e.headers.get("Retry-After", "30"))
                    logger.warning(f"Discord rate limited — waiting {retry}s")
                    await asyncio.sleep(retry)
                else:
                    logger.error(f"Discord API {e.code}: {e.reason}")
                    await asyncio.sleep(30)
            except Exception as e:
                logger.error(f"Discord poll error: {e}")
                await asyncio.sleep(30)

            await asyncio.sleep(self.poll_interval)

    async def _poll(self) -> None:
        """Poll for new messages."""
        if not self.last_message_id:
            return
        msgs = await asyncio.to_thread(
            self._get,
            f"/channels/{self.channel_id}/messages?after={self.last_message_id}&limit=50",
        )
        if not msgs:
            return
        msgs.sort(key=lambda m: int(m["id"]))
        for msg in msgs:
            self.last_message_id = msg["id"]
            if msg.get("type", 0) != 0:
                continue
            author = msg.get("author", {})
            if author.get("bot"):
                continue
            if self.allowed_users and int(author.get("id", 0)) not in self.allowed_users:
                continue
            asyncio.create_task(self._on_message(msg))

    async def _on_message(self, msg: dict) -> None:
        """Handle an incoming Discord message."""
        text = msg.get("content", "").strip()
        if not text:
            return

        author = msg.get("author", {})
        user_id = str(author.get("id", ""))

        if self.debouncer.should_skip(user_id, self.channel_id, text):
            logger.debug(f"Debounced rapid message from {user_id} in {self.channel_id}")
            return

        session_key = f"dc_{self.channel_id}"

        # Delegate to base class message handler
        await self.handle_incoming(
            text=text,
            channel_id=self.channel_id,
            channel_name=self.channel_id,
            user_id=user_id,
            post_id=msg.get("id"),
            session_key=session_key,
        )

    # ── Platform overrides ────────────────────────────────────────────────

    async def send(self, channel_id: str, text: str, reply_to: Optional[str] = None) -> None:
        """Post a message to Discord."""
        try:
            data = {"content": text}
            if reply_to:
                data["message_reference"] = {"message_id": reply_to}
            await asyncio.to_thread(
                self._post,
                f"/channels/{channel_id}/messages",
                data,
            )
        except Exception as e:
            logger.error(f"Discord send failed: {e}")

    async def _send_placeholder(
        self, channel_id: str, tier_display: str, reply_to: Optional[str] = None
    ) -> str:
        """Post thinking placeholder."""
        try:
            data = {"content": f"_thinking ({tier_display})..._"}
            if reply_to:
                data["message_reference"] = {"message_id": reply_to}
            msg = await asyncio.to_thread(
                self._post,
                f"/channels/{channel_id}/messages",
                data,
            )
            return msg.get("id", "")
        except Exception:
            return ""

    async def _update_placeholder(self, placeholder_id: str, text: str,
                                 channel_id: Optional[str] = None) -> None:
        """Update thinking placeholder."""
        try:
            ch_id = channel_id or self.channel_id
            await asyncio.to_thread(
                self._patch,
                f"/channels/{ch_id}/messages/{placeholder_id}",
                {"content": text},
            )
        except Exception:
            pass

    async def deliver(self, outbound) -> None:
        """Engine callback — post autonomously-generated response."""
        chunks = self.fmt.format_response(outbound.content)
        channel = outbound.channel_id or self.channel_id
        for chunk in chunks:
            await asyncio.to_thread(
                self._post,
                f"/channels/{channel}/messages",
                {"content": chunk},
            )
