"""
Discord adapter — REST API polling.

Refactored from claude-brain for provider-agnostic operation.
Push-only platforms (alerts, builds) use Discord; this adapter
handles interactive responses when Discord is the primary interface.
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Optional, Set

from ...core.behaviors import NexusBehavior, tier_label
from ...core.bridge import NexusBridge
from ...core.commands import CommandRegistry
from ...core.formatter import PlatformFormatter
from ...core.session import SessionStore

logger = logging.getLogger(__name__)

DISCORD_API = "https://discord.com/api/v10"


class DiscordAdapter:
    """
    Discord adapter using REST polling.

    Monitors a channel for new messages, routes them through the
    provider bridge, and posts responses back.
    """

    def __init__(self, config: dict, bridge: NexusBridge, sessions: SessionStore,
                 behavior: NexusBehavior):
        self.config = config
        self.bridge = bridge
        self.sessions = sessions
        self.behavior = behavior

        dc = config.get("discord", config)
        self.token = dc.get("token", "")
        self.channel_id = str(dc.get("channel_id", ""))
        self.allowed_users: Set[int] = set(dc.get("allowed_users", []))
        self.poll_interval: int = dc.get("poll_interval", 10)
        self.last_message_id: Optional[str] = None

        self.commands = CommandRegistry("discord")
        self.fmt = PlatformFormatter("discord")
        self._stop = asyncio.Event()

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
        req = urllib.request.Request(f"{DISCORD_API}{path}", data=body, headers=self._headers(), method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    def _patch(self, path: str, data: dict) -> dict:
        body = json.dumps(data).encode()
        req = urllib.request.Request(f"{DISCORD_API}{path}", data=body, headers=self._headers(), method="PATCH")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    async def run(self) -> None:
        if not self.token or not self.channel_id:
            logger.warning("Discord not configured — adapter disabled")
            return

        try:
            msgs = await asyncio.to_thread(
                self._get, f"/channels/{self.channel_id}/messages?limit=1"
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

    async def stop(self) -> None:
        self._stop.set()

    async def _poll(self) -> None:
        if not self.last_message_id:
            return
        msgs = await asyncio.to_thread(
            self._get,
            f"/channels/{self.channel_id}/messages?after={self.last_message_id}&limit=50"
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
            asyncio.create_task(self._handle(msg))

    async def _handle(self, msg: dict) -> None:
        text = msg.get("content", "").strip()
        if not text:
            return

        session_key = f"dc_{self.channel_id}"

        if self.commands.is_command(text):
            await self._handle_command(text, msg, session_key)
            return

        await self.sessions.mark_active(session_key)
        triage = await self.behavior.route_message(text, session_key, "discord")
        tier_display = tier_label(triage.tier)

        try:
            ack = await asyncio.to_thread(
                self._post,
                f"/channels/{self.channel_id}/messages",
                {"content": f"_thinking ({tier_display})..._",
                 "message_reference": {"message_id": msg["id"]}},
            )
        except Exception as e:
            logger.error(f"Discord ack failed: {e}")
            return

        prompt = f"[Platform: Discord | Channel: {self.channel_id}]\n{text}"

        try:
            result = await self.bridge.invoke(
                prompt=prompt,
                session_key=session_key,
                tier=triage.tier,
                task_type=triage.provider_key,
            )
        except Exception as e:
            logger.error(f"Discord invoke error: {e}")
            try:
                await asyncio.to_thread(
                    self._patch,
                    f"/channels/{self.channel_id}/messages/{ack['id']}",
                    {"content": "_(error processing your request)_"},
                )
            except Exception:
                pass
            return

        response = result.text or "_(no response)_"
        chunks = self.fmt.format_response(response)

        try:
            await asyncio.to_thread(
                self._patch,
                f"/channels/{self.channel_id}/messages/{ack['id']}",
                {"content": chunks[0]},
            )
        except Exception:
            pass

        for chunk in chunks[1:]:
            await asyncio.sleep(0.5)
            try:
                await asyncio.to_thread(
                    self._post,
                    f"/channels/{self.channel_id}/messages",
                    {"content": chunk},
                )
            except Exception:
                pass

    async def _handle_command(self, text: str, msg: dict, session_key: str) -> None:
        cmd, args = self.commands.parse(text)
        if not cmd:
            return

        reply = lambda content: asyncio.to_thread(
            self._post,
            f"/channels/{self.channel_id}/messages",
            {"content": content, "message_reference": {"message_id": msg["id"]}},
        )

        if cmd.behavioral:
            event = self.behavior.handle_command(text, session_key, "discord")
            if event:
                await reply(f"_{event.detail}_")
            return

        name = cmd.name

        if name in ("new", "reset"):
            self.bridge.clear_session(session_key)
            await self.sessions.clear(session_key)
            await reply("Session cleared.")

        elif name == "status":
            status = self.behavior.get_status()
            hist = self.bridge.get_history_length(session_key)
            await reply(
                f"Tier: {tier_label(status.get('tier_override') or 'standard')}"
                + (" (auto)" if status["auto_triage"] else " (locked)") + "\n"
                f"History: {hist} messages"
            )

        elif name == "providers":
            names = list(self.bridge.router.providers.keys())
            await reply("Providers: " + ", ".join(f"`{n}`" for n in names))

        elif name == "help":
            await reply(self.commands.help_text())

    async def deliver(self, outbound) -> None:
        """Engine callback — post an autonomously-generated response to a channel."""
        chunks = self.fmt.format_response(outbound.content)
        channel = outbound.channel_id or self.channel_id
        for chunk in chunks:
            await asyncio.to_thread(
                self._post,
                f"/channels/{channel}/messages",
                {"content": chunk},
            )
