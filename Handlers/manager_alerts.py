from __future__ import annotations

import html
import logging

from aiogram import Bot

logger = logging.getLogger(__name__)


async def notify_manager_about_phone_received(
    bot: Bot,
    manager_chat_id: int,
    username: str | None,
    phone_number: str,
) -> None:
    safe_username = html.escape(username) if username else "без username"
    text = (
        "✅ <b>НОМЕР ТЕЛЕФОНА ПОЛУЧЕН!</b>\n\n"
        f"Клиент @{safe_username} готов купить, оставил телефон: <code>{phone_number}</code>.\n"
        "Свяжитесь с ним!"
    )
    await bot.send_message(chat_id=manager_chat_id, text=text)
    logger.info("Менеджер %s уведомлён о получении телефона от @%s", manager_chat_id, username)
