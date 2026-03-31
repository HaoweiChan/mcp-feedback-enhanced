"""
Telegram notification service using aiohttp directly.

Avoids python-telegram-bot's lifecycle conflicts when embedded in
an existing asyncio event loop (e.g. FastMCP). All API calls are
plain HTTP requests via aiohttp which is already a project dependency.
"""

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
import inspect

import aiohttp


TG_API = "https://api.telegram.org/bot{token}/{method}"

FEEDBACK_BUTTONS = {
    "inline_keyboard": [
        [
            {"text": "👍 Continue", "callback_data": "feedback:continue"},
            {"text": "⏭️ Skip", "callback_data": "feedback:skip"},
        ],
        [
            {"text": "🔁 Retry", "callback_data": "feedback:retry"},
            {"text": "✋ Wait", "callback_data": "feedback:wait"},
        ],
    ]
}


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
    def __init__(
        self,
        bot_token: str,
        admin_chat_id: str,
        on_feedback_received: Callable[[str], None] | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._admin_chat_id = admin_chat_id
        self._on_feedback_received = on_feedback_received
        self._session: aiohttp.ClientSession | None = None
        self._pending_request: PendingFeedbackRequest | None = None
        self._poll_task: asyncio.Task | None = None

    @property
    def admin_chat_id(self) -> str:
        return self._admin_chat_id

    def set_pending_request(self, request: PendingFeedbackRequest) -> None:
        self._pending_request = request

    def clear_pending_request(self) -> None:
        self._pending_request = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        if self._session is not None:
            return
        self._session = aiohttp.ClientSession()
        self._poll_task = asyncio.create_task(self._run_polling())

    async def stop(self) -> None:
        if self._poll_task is not None:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        if self._session is not None:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------ #
    # Low-level HTTP
    # ------------------------------------------------------------------ #

    async def _call(self, method: str, **kwargs: Any) -> Any:
        """Call Telegram Bot API via aiohttp. Returns parsed JSON result."""
        if self._session is None:
            raise RuntimeError("Session not started")
        url = TG_API.format(token=self._bot_token, method=method)
        async with self._session.post(url, json=kwargs) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Telegram API error: {data}")
            return data["result"]

    # ------------------------------------------------------------------ #
    # Polling
    # ------------------------------------------------------------------ #

    async def _run_polling(self) -> None:
        """Long-poll for incoming updates and dispatch them."""
        offset = None
        try:
            while True:
                try:
                    updates = await self._call(
                        "getUpdates",
                        offset=offset,
                        timeout=20,
                        allowed_updates=["message", "callback_query"],
                    )
                    for u in updates:
                        offset = u["update_id"] + 1
                        asyncio.create_task(self._dispatch_update(u))
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    from ..debug import debug_log
                    debug_log(f"Telegram polling error: {e}")
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass

    async def _dispatch_update(self, u: dict) -> None:
        if "callback_query" in u:
            await self._handle_callback(u["callback_query"])
        elif "message" in u:
            msg = u["message"]
            text = msg.get("text", "")
            if text.startswith("/start"):
                await self._handle_start(msg)
            else:
                await self._handle_message(msg)

    # ------------------------------------------------------------------ #
    # Handlers
    # ------------------------------------------------------------------ #

    async def _handle_start(self, msg: dict) -> None:
        if str(msg["chat"]["id"]) != self._admin_chat_id:
            return
        await self._call(
            "sendMessage",
            chat_id=self._admin_chat_id,
            text="✅ *Bot Connected!*\n\nI'll notify you when feedback is requested.",
            parse_mode="Markdown",
        )

    async def _handle_callback(self, query: dict) -> None:
        chat_id = str(query["message"]["chat"]["id"])
        if chat_id != self._admin_chat_id:
            await self._call("answerCallbackQuery", callback_query_id=query["id"], text="Unauthorized")
            return

        await self._call("answerCallbackQuery", callback_query_id=query["id"])

        data = query.get("data", "")
        if not data.startswith("feedback:"):
            return

        feedback_type = data.split(":", 1)[1]
        
        if feedback_type == "wait":
            await self._call(
                "editMessageText",
                chat_id=chat_id,
                message_id=query["message"]["message_id"],
                text="⏳ *Waiting for manual text response...*\n\nPlease type your reply in the chat.",
                parse_mode="Markdown",
            )
            return

        feedback_map = {
            "continue": "Looks good, please continue.",
            "skip": "Skip this step.",
            "retry": "Please try again.",
        }
        feedback_text = feedback_map.get(feedback_type, "")

        if feedback_text and self._on_feedback_received:
            res = self._on_feedback_received(feedback_text)
            if inspect.isawaitable(res):
                await res
            await self._call(
                "editMessageText",
                chat_id=chat_id,
                message_id=query["message"]["message_id"],
                text="✅ *Feedback Submitted!*\n\n" + feedback_text,
                parse_mode="Markdown",
            )
            self.clear_pending_request()
        else:
            await self._call(
                "editMessageText",
                chat_id=chat_id,
                message_id=query["message"]["message_id"],
                text="⚠️ No active feedback request.",
                parse_mode="Markdown",
            )

    async def _handle_message(self, msg: dict) -> None:
        if str(msg["chat"]["id"]) != self._admin_chat_id:
            return

        if self._pending_request is None:
            await self._call(
                "sendMessage",
                chat_id=self._admin_chat_id,
                text="⚠️ No active feedback request to respond to.",
                parse_mode="Markdown",
            )
            return

        feedback_text = msg.get("text", "")
        if feedback_text and self._on_feedback_received:
            res = self._on_feedback_received(feedback_text)
            if inspect.isawaitable(res):
                await res
            await self._call(
                "sendMessage",
                chat_id=self._admin_chat_id,
                text=f"✅ *Feedback Submitted!*\n\n{feedback_text}",
                parse_mode="Markdown",
            )
            self.clear_pending_request()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def send_feedback_request(
        self,
        project_directory: str,
        summary: str,
        timeout_seconds: int,
    ) -> bool:
        if self._session is None:
            return False

        import datetime as dt
        now = dt.datetime.now(dt.UTC)
        expires_at_aware = now + dt.timedelta(seconds=timeout_seconds)
        expires_at = expires_at_aware.replace(tzinfo=None)

        def _build_message(remaining_seconds: int) -> str:
            mins = remaining_seconds // 60
            secs = remaining_seconds % 60
            expire_str = expires_at_aware.astimezone().strftime("%H:%M:%S")
            timer_line = f"⏳ *{mins}m {secs:02d}s remaining* — expires at {expire_str}"
            return f"{summary}\n\n{timer_line}"

        try:
            result = await self._call(
                "sendMessage",
                chat_id=self._admin_chat_id,
                text=_build_message(timeout_seconds),
                parse_mode="Markdown",
                reply_markup=FEEDBACK_BUTTONS,
            )
            msg_id = result["message_id"]
            self._pending_request = PendingFeedbackRequest(
                project_directory=project_directory,
                summary=summary,
                expires_at=expires_at,
                message_id=msg_id,
            )

            # Live countdown updater
            asyncio.create_task(
                self._run_countdown_updater(
                    message_id=msg_id,
                    timeout_seconds=timeout_seconds,
                    build_message=_build_message,
                )
            )
            return True
        except Exception as e:
            from ..debug import debug_log
            debug_log(f"Telegram send_feedback_request failed: {e}")
            return False

    async def _run_countdown_updater(
        self,
        message_id: int,
        timeout_seconds: int,
        build_message: Callable[[int], str],
        interval: int = 30,
    ) -> None:
        """Edit the message every `interval` seconds to show live countdown."""
        elapsed = 0
        while elapsed < timeout_seconds:
            await asyncio.sleep(interval)
            elapsed += interval
            if self._pending_request is None:
                return
            remaining = max(0, timeout_seconds - elapsed)
            try:
                await self._call(
                    "editMessageText",
                    chat_id=self._admin_chat_id,
                    message_id=message_id,
                    text=build_message(remaining),
                    parse_mode="Markdown",
                    reply_markup=FEEDBACK_BUTTONS,
                )
            except Exception:
                return

    async def send_confirmation(self, feedback: str) -> bool:
        try:
            await self._call(
                "sendMessage",
                chat_id=self._admin_chat_id,
                text=f"✅ *Feedback Received!*\n\n{feedback}",
                parse_mode="Markdown",
            )
            return True
        except Exception:
            return False

    async def send_timeout_notification(self) -> bool:
        try:
            await self._call(
                "sendMessage",
                chat_id=self._admin_chat_id,
                text="⌛ *Feedback Timed Out*\n\nNo feedback was received within the timeout period.",
                parse_mode="Markdown",
            )
            return True
        except Exception:
            return False

    async def send_custom_message(self, text: str) -> bool:
        try:
            await self._call(
                "sendMessage",
                chat_id=self._admin_chat_id,
                text=text,
                parse_mode="Markdown",
            )
            return True
        except Exception:
            return False
