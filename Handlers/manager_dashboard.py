from __future__ import annotations

import html

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from DB.database import Database

router = Router(name="manager_dashboard")

STATUS_LABELS = {
    "new": "🆕 Новый",
    "notified": "📨 Уведомлён",
    "phone_requested": "📞 Запрошен телефон",
    "phone_received": "✅ Телефон получен",
    "closed": "🔒 Закрыт",
}


@router.message(Command("mystats"))
async def my_stats_handler(message: Message, db: Database) -> None:
    if message.from_user is None:
        return

    client = await db.get_client_by_manager_chat_id(message.from_user.id)
    if client is None:
        await message.answer(
            "Эта команда для менеджеров подключённых аккаунтов. "
            "Если это ошибка — свяжитесь с владельцем бота."
        )
        return

    stats = await db.get_leads_stats_for_manager(message.from_user.id)
    recent = await db.get_recent_leads_for_manager(message.from_user.id, limit=5)

    lines = [
        f"📊 <b>Статистика {html.escape(client['name'])}</b>\n",
        f"Всего лидов: <b>{stats['total']}</b>",
        f"🌿 Органика: {stats['organic']}",
        f"🎯 Таргет: {stats['ad']}",
    ]

    if recent:
        lines.append("\n<b>Последние лиды:</b>")
        for lead in recent:
            source = "🎯" if lead["post_type"] == "ad" else "🌿"
            phone = lead["phone_number"] or "—"
            status_label = STATUS_LABELS.get(lead["status"], lead["status"])
            username = html.escape(lead["ig_username"]) if lead["ig_username"] else "?"
            lines.append(
                f"{source} @{username} — {status_label}, тел: {phone} — {lead['created_at']:%d.%m %H:%M}"
            )

    await message.answer("\n".join(lines))
