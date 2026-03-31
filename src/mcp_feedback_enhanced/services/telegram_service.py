import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)


BOT_STARTED = False


@dataclass
class TelegramServiceConfig:
    bot_token: str = ""
    admin_chat_id: str = ""
    enabled: bool = False


@dataclass
class PendingFeedbackRequest:
    project_directory: str = ""
    summary: str = ""
    expires_at: datetime = field(default_factory=datetime.utcnow)
    message_id: int | None = None


class TelegramServiceManager:
    _instance: "TelegramServiceManager | None" = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __init__(self) -> None:
        self._service: TelegramService | None = None
        self._config = TelegramServiceConfig(
            bot_token=os.getenv("MCP_TELEGRAM_BOT_TOKEN", ""),
            admin_chat_id=os.getenv("MCP_TELEGRAM_ADMIN_CHAT_ID", ""),
            enabled=os.getenv("MCP_TELEGRAM_ENABLED", "").lower()
            in ("true", "1", "yes", "on"),
        )

    @classmethod
    async def get_instance(cls) -> "TelegramServiceManager":
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def config(self) -> TelegramServiceConfig:
        return self._config

    @property
    def is_enabled(self) -> bool:
        return self._config.enabled and bool(
            self._config.bot_token and self._config.admin_chat_id
        )

    def get_service(self) -> "TelegramService | None":
        return self._service

    async def start_service(
        self,
        on_feedback_received: Callable[[str], None] | None = None,
    ) -> "TelegramService | None":
        if not self.is_enabled:
            return None

        if self._service is not None:
            return self._service

        self._service = TelegramService(
            bot_token=self._config.bot_token,
            admin_chat_id=self._config.admin_chat_id,
            on_feedback_received=on_feedback_received,
        )
        await self._service.start()
        return self._service

    async def stop_service(self) -> None:
        if self._service is not None:
            await self._service.stop()
            self._service = None


class TelegramService:
    FEEDBACK_BUTTONS = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Approve", callback_data="feedback:approve"),
                InlineKeyboardButton(
                    "❌ Request Changes", callback_data="feedback:changes"
                ),
            ],
            [
                InlineKeyboardButton("💬 Comment", callback_data="feedback:comment"),
            ],
        ]
    )

    def __init__(
        self,
        bot_token: str,
        admin_chat_id: str,
        on_feedback_received: Callable[[str], None] | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._admin_chat_id = admin_chat_id
        self._on_feedback_received = on_feedback_received
        self._application: Application | None = None
        self._pending_request: PendingFeedbackRequest | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._poll_task: asyncio.Task | None = None

    @property
    def admin_chat_id(self) -> str:
        return self._admin_chat_id

    def set_pending_request(self, request: PendingFeedbackRequest) -> None:
        self._pending_request = request

    def clear_pending_request(self) -> None:
        self._pending_request = None

    async def start(self) -> None:
        if self._application is not None:
            return

        self._application = Application.builder().token(self._bot_token).build()

        self._application.add_handler(CommandHandler("start", self._handle_start))
        self._application.add_handler(CallbackQueryHandler(self._handle_callback))
        self._application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

        await self._application.initialize()
        await self._application.start()
        self._poll_task = asyncio.create_task(self._run_polling())

    async def stop(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        if self._application is not None:
            await self._application.stop()
            await self._application.shutdown()
            self._application = None

    async def _run_polling(self) -> None:
        if self._application is None:
            return

        try:
            async with self._application:
                await self._application.run_polling(
                    allowed_updates=["message", "callback_query"]
                )
        except asyncio.CancelledError:
            pass

    async def _handle_start(self, update: Update) -> None:
        if not update.message or str(update.message.chat.id) != self._admin_chat_id:
            return

        await update.message.reply_text(
            "✅ *Bot Connected!*\n\nI'll notify you when feedback is requested.",
            parse_mode="Markdown",
        )

    async def _handle_callback(self, update: Update) -> None:
        query = update.callback_query
        if query is None:
            return

        if str(query.message.chat.id) != self._admin_chat_id:
            await query.answer("Unauthorized", show_alert=False)
            return

        await query.answer()

        data = query.data or ""
        if not data.startswith("feedback:"):
            return

        feedback_type = data.split(":", 1)[1]
        feedback_text = self._get_feedback_text(feedback_type)

        if feedback_text and self._on_feedback_received:
            self._on_feedback_received(feedback_text)
            await query.message.edit_text(
                "✅ *Feedback Received!*\n\nYour feedback has been submitted.",
                parse_mode="Markdown",
            )
        else:
            await query.message.edit_text(
                "⚠️ No active feedback request.",
                parse_mode="Markdown",
            )

    async def _handle_message(self, update: Update) -> None:
        if not update.message or str(update.message.chat.id) != self._admin_chat_id:
            return

        if self._pending_request is None:
            await update.message.reply_text(
                "⚠️ No active feedback request to respond to.",
                parse_mode="Markdown",
            )
            return

        feedback_text = update.message.text
        if feedback_text and self._on_feedback_received:
            self._on_feedback_received(feedback_text)
            await update.message.reply_text(
                "✅ *Feedback Received!*\n\nYour feedback has been submitted.",
                parse_mode="Markdown",
            )
            self.clear_pending_request()

    def _get_feedback_text(self, feedback_type: str) -> str:
        feedback_map = {
            "approve": "✅ Approved",
            "changes": "❌ Requested changes",
        }
        return feedback_map.get(feedback_type, "")

    async def send_feedback_request(
        self,
        project_directory: str,
        summary: str,
        timeout_seconds: int,
    ) -> bool:
        if self._application is None:
            return False

        expires_at = datetime.now(UTC).replace(tzinfo=None) + __import__(
            "datetime"
        ).timedelta(seconds=timeout_seconds)
        expires_time = expires_at.strftime("%I:%M %p")

        message_text = (
            "🤖 *Feedback Request*\n\n"
            f"📁 *Project:* `{project_directory}`\n\n"
            f"📝 *Summary:*\n{summary}\n\n"
            f"⏱️ *Expires at {expires_time}*\n\n"
            "Reply with your feedback or tap a button above."
        )

        try:
            sent_message = await self._application.bot.send_message(
                chat_id=self._admin_chat_id,
                text=message_text,
                parse_mode="Markdown",
                reply_markup=self.FEEDBACK_BUTTONS,
            )
            self._pending_request = PendingFeedbackRequest(
                project_directory=project_directory,
                summary=summary,
                expires_at=expires_at,
                message_id=sent_message.message_id,
            )
            return True
        except Exception:
            return False

    async def send_confirmation(self, feedback: str) -> bool:
        if self._application is None:
            return False

        try:
            await self._application.bot.send_message(
                chat_id=self._admin_chat_id,
                text=f"✅ *Feedback Received!*\n\n{feedback}",
                parse_mode="Markdown",
            )
            return True
        except Exception:
            return False

    async def send_timeout_notification(self) -> bool:
        if self._application is None:
            return False

        try:
            await self._application.bot.send_message(
                chat_id=self._admin_chat_id,
                text="⌛ *Feedback Timed Out*\n\nNo feedback was received within the timeout period.",
                parse_mode="Markdown",
            )
            return True
        except Exception:
            return False

    async def send_custom_message(self, text: str) -> bool:
        if self._application is None:
            return False

        try:
            await self._application.bot.send_message(
                chat_id=self._admin_chat_id,
                text=text,
                parse_mode="Markdown",
            )
            return True
        except Exception:
            return False
