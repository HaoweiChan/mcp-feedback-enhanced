#!/usr/bin/env python3
"""
MCP Interactive Feedback Enhanced
 ==================================

互動式用戶回饋 MCP 伺服器，提供 AI 輔助開發中的回饋收集功能。

作者: Fábio Ferreira
增強功能: Web UI 支援、圖片上傳、現代化界面設計

特色：
- Web UI 介面支援
- 智慧環境檢測
- 命令執行功能
- 圖片上傳支援
- 現代化深色主題
- 重構的模組化架構
"""

__version__ = "2.6.0"
__author__ = "Minidoracat"
__email__ = "minidora0702@gmail.com"

import os

from .server import main as run_server
from .services import TelegramService, TelegramServiceManager
from .web import WebUIManager, get_web_ui_manager, launch_web_feedback_ui, stop_web_ui


# Telegram 配置常量
MCP_TELEGRAM_BOT_TOKEN = os.getenv("MCP_TELEGRAM_BOT_TOKEN", "")
MCP_TELEGRAM_ADMIN_CHAT_ID = os.getenv("MCP_TELEGRAM_ADMIN_CHAT_ID", "")
MCP_TELEGRAM_ENABLED = os.getenv("MCP_TELEGRAM_ENABLED", "").lower() in (
    "true",
    "1",
    "yes",
    "on",
)

# 保持向後兼容性
feedback_ui = None

__all__ = [
    "MCP_TELEGRAM_ADMIN_CHAT_ID",
    "MCP_TELEGRAM_BOT_TOKEN",
    "MCP_TELEGRAM_ENABLED",
    "TelegramService",
    "TelegramServiceManager",
    "WebUIManager",
    "__author__",
    "__version__",
    "feedback_ui",
    "get_web_ui_manager",
    "launch_web_feedback_ui",
    "run_server",
    "stop_web_ui",
]


def main():
    """主要入口點，用於 uvx 執行"""
    from .__main__ import main as cli_main

    return cli_main()
