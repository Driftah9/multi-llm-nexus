"""
Telegram adapter — Bot API with forum topics support.

Refactored from claude-brain for provider-agnostic operation.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Set

from ...core.behaviors import NexusBehavior, tier_label
from ...core.bridge import NexusBridge
from ...core.commands import CommandRegistry
from ...core.formatter import PlatformFormatter
from ...core.session import SessionStore

logger = logging.getLogger(__name__)

try:
    from telegram import Update, BotCommand
    from telegram.ext import Application, MessageHandler, ContextTypes, filters
    from telegram.constants import ParseMode, ChatAction
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False


class TelegramAdapter:
    """
    Telegram adapter using python-telegram-bot.

    Supports forum topic threads as separate session spaces.
    """

    def __init__(self, config: dict, bridge: NexusBridge, sessions: SessionStore,
                 behavior: NexusBehavior):
        self.config = config
        self.bridge = bridge
        self.sessions = sessions
        self.behavior = behavior

        tg = config.get("telegram", config)
        self.token = tg.get("token", "")
        self.allowed_users: Set[int] = set(tg.get("allowed_users", []))
        self.chat_id: Optional[int] = tg.get("chat_id")
        self._app: Optional["Application"] = None
        self._stop = asyncio.Event()

        self.commands = CommandRegistry("telegram")
        self.fmt = PlatformFormatter("telegram")

    async def run(self) -> None:
        if not TELEGRAM_AVAILABLE:
            logger.error("python-telegram-bot not installed: pip install python-telegram-bot")
            return
        if not self.token:
            logger.warning("No Telegram token — adapter disabled")
            return

        app = Application.builder().token(self.token).build()
        self._app = app

        app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, self._handle_message
        ))

        bot_commands = [
            BotCommand("new",    "Start fresh session"),
            BotCommand("status", "Show session info"),
            BotCommand("help",   "Show commands"),
        ]

        async with app:
            await app.bot.set_my_commands(bot_commands)
            await app.start()
            logger.info("Telegram adapter started")
            await app.updater.start_polling(drop_pending_updates=True)
            await self._stop.wait()

    async def stop(self) -> None:
        self._stop.set()
        if self._app:
            try:
                await self._app.updater.stop()
                await self._app.stop()
            except Exception:
                pass

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if not msg or not update.effective_user:
            return

        user_id = update.effective_user.id
        if self.allowed_users and user_id not in self.allowed_users:
            return

        chat_id = msg.chat_id
        thread_id = msg.message_thread_id
        text = msg.text or msg.caption or ""

        if self.commands.is_command(text):
            await self._handle_command(text, msg, chat_id, thread_id)
            return

        session_key = f"tg_{chat_id}_{thread_id or 'main'}"
        await self.sessions.mark_active(session_key)

        triage = await self.behavior.route_message(text, session_key, "telegram")
        tier_display = tier_label(triage.tier)

        ack = await msg.reply_text(
            f"_thinking ({tier_display})..._",
            parse_mode=ParseMode.MARKDOWN,
        )
        asyncio.create_task(self._send_typing(chat_id))

        topic_name = f"topic-{thread_id}" if thread_id else "general"
        prompt = f"[Platform: Telegram | Topic: {topic_name}]\n{text}"

        try:
            result = await self.bridge.invoke(
                prompt=prompt,
                session_key=session_key,
                tier=triage.tier,
                task_type=triage.provider_key,
            )
        except Exception as e:
            logger.error(f"Telegram invoke error: {e}")
            await ack.edit_text("_(error processing your request)_")
            return

        response = result.text or "_(no response)_"
        chunks = self.fmt.format_response(response)

        try:
            await ack.edit_text(chunks[0])
        except Exception:
            await ack.edit_text(chunks[0][:4000])

        for chunk in chunks[1:]:
            await msg.reply_text(chunk, message_thread_id=thread_id)

    async def _handle_command(self, text: str, msg, chat_id: int,
                               thread_id: Optional[int]) -> None:
        cmd, args = self.commands.parse(text)
        if not cmd:
            return

        session_key = f"tg_{chat_id}_{thread_id or 'main'}"

        if cmd.behavioral:
            event = self.behavior.handle_command(text, session_key, "telegram")
            if event:
                await msg.reply_text(f"_{event.detail}_", message_thread_id=thread_id)
            return

        name = cmd.name

        if name in ("new", "reset"):
            self.bridge.clear_session(session_key)
            await self.sessions.clear(session_key)
            await msg.reply_text("Session cleared.", message_thread_id=thread_id)

        elif name == "status":
            status = self.behavior.get_status()
            hist = self.bridge.get_history_length(session_key)
            await msg.reply_text(
                f"Tier: {tier_label(status.get('tier_override') or 'standard')}"
                + (" (auto)" if status["auto_triage"] else " (locked)") + "\n"
                f"History: {hist} messages",
                message_thread_id=thread_id,
            )

        elif name == "providers":
            names = list(self.bridge.router.providers.keys())
            await msg.reply_text(
                "Providers: " + ", ".join(f"`{n}`" for n in names),
                message_thread_id=thread_id,
            )

        elif name == "help":
            await msg.reply_text(self.commands.help_text(), message_thread_id=thread_id)

    async def _send_typing(self, chat_id: int) -> None:
        try:
            for _ in range(60):
                await self._app.bot.send_chat_action(chat_id, ChatAction.TYPING)
                await asyncio.sleep(5)
        except (asyncio.CancelledError, Exception):
            pass

    async def deliver(self, outbound) -> None:
        """Engine callback — post an autonomously-generated response to a chat."""
        if not self._app:
            logger.warning("Telegram deliver: app not running, dropping outbound message")
            return
        chunks = self.fmt.format_response(outbound.content)
        for chunk in chunks:
            try:
                await self._app.bot.send_message(chat_id=int(outbound.channel_id), text=chunk)
            except Exception as e:
                logger.error(f"Telegram deliver failed: {e}")
                break
