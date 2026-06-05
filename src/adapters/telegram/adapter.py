"""
Telegram adapter — Bot API with forum topics support.

Inherits all message handling, triage, and command logic from AdapterBase.
Only implements Telegram-specific: bot connection and message sending.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Set

from ..adapter_base import AdapterBase
from ...core.behaviors import NexusBehavior
from ...core.bridge import NexusBridge
from ...core.session import SessionStore

logger = logging.getLogger(__name__)

try:
    from telegram import Update, BotCommand
    from telegram.ext import Application, MessageHandler, ContextTypes, filters
    from telegram.constants import ParseMode, ChatAction

    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False


class TelegramAdapter(AdapterBase):
    """
    Telegram adapter using python-telegram-bot.

    Inherits from AdapterBase:
      - Triage (5-dimension message classification)
      - Session management
      - Command dispatch
      - Bridge invocation with pool routing

    Implements Telegram-specific:
      - Bot polling
      - Forum topic threading as separate sessions
    """

    PLATFORM_PROPS = {
        "platform_name": "telegram",
        "max_chars": 4096,
        "markdown_support": "partial",
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

        tg = config.get("telegram", config)
        self.token = tg.get("token", "")
        self.allowed_users: Set[int] = set(tg.get("allowed_users", []))
        self.chat_id: Optional[int] = tg.get("chat_id")
        self._app: Optional["Application"] = None

    async def run(self) -> None:
        """Start bot and listen for messages."""
        if not TELEGRAM_AVAILABLE:
            logger.error("python-telegram-bot not installed: pip install python-telegram-bot")
            return
        if not self.token:
            logger.warning("No Telegram token — adapter disabled")
            return

        app = Application.builder().token(self.token).build()
        self._app = app

        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message)
        )

        bot_commands = [
            BotCommand("new", "Start fresh session"),
            BotCommand("status", "Show session info"),
            BotCommand("help", "Show commands"),
        ]

        async with app:
            await app.bot.set_my_commands(bot_commands)
            await app.start()
            logger.info("Telegram adapter started")
            await app.updater.start_polling(drop_pending_updates=True)
            await self._stop.wait()

    async def _on_message(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Handle incoming Telegram message."""
        msg = update.effective_message
        if not msg or not update.effective_user:
            return

        user_id = update.effective_user.id
        if self.allowed_users and user_id not in self.allowed_users:
            return

        chat_id = msg.chat_id
        thread_id = msg.message_thread_id
        text = msg.text or msg.caption or ""

        session_key = f"tg_{chat_id}_{thread_id or 'main'}"

        # Delegate to base class message handler
        await self.handle_incoming(
            text=text,
            channel_id=str(chat_id),
            channel_name=f"topic-{thread_id}" if thread_id else "general",
            user_id=str(user_id),
            post_id=str(msg.message_id),
            session_key=session_key,
        )

    # ── Platform overrides ────────────────────────────────────────────────

    async def send(self, channel_id: str, text: str, reply_to: Optional[str] = None) -> None:
        """Post a message to Telegram."""
        if not self._app:
            return
        try:
            chat_id = int(channel_id)
            await self._app.bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")

    async def _send_placeholder(
        self, channel_id: str, tier_display: str, reply_to: Optional[str] = None
    ) -> str:
        """Post thinking placeholder."""
        if not self._app:
            return ""
        try:
            msg = await self._app.bot.send_message(
                chat_id=int(channel_id),
                text=f"_thinking ({tier_display})..._",
                parse_mode=ParseMode.MARKDOWN,
            )
            return str(msg.message_id)
        except Exception:
            return ""

    async def _update_placeholder(self, placeholder_id: str, text: str,
                                 channel_id: Optional[str] = None) -> None:
        """Update thinking placeholder."""
        if not self._app:
            return
        try:
            chat_id = int(channel_id) if channel_id else (int(self.chat_id) if self.chat_id else 0)
            msg_id = int(placeholder_id)
            await self._app.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
            )
        except Exception:
            pass

    async def _stop_typing(self, channel_id: str) -> None:
        """Stop typing indicator."""
        if not self._app:
            return
        try:
            await self._app.bot.send_chat_action(int(channel_id), ChatAction.TYPING)
        except Exception:
            pass

    async def deliver(self, outbound) -> None:
        """Engine callback — post autonomously-generated response."""
        if not self._app:
            logger.warning("Telegram deliver: app not running")
            return
        chunks = self.fmt.format_response(outbound.content)
        for chunk in chunks:
            try:
                await self._app.bot.send_message(
                    chat_id=int(outbound.channel_id), text=chunk
                )
            except Exception as e:
                logger.error(f"Telegram deliver failed: {e}")
                break
