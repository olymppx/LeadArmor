from __future__ import annotations

import html

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from DB.database import Database

# Вынесено из Handlers/manager_dashboard.py в отдельный top-level модуль
# намеренно: и Handlers/onboarding.py, и API/instagram_oauth.py должны
# показывать один и тот же "домашний экран" менеджера, а импорт чего-либо
# из пакета Handlers всегда сначала выполняет Handlers/__init__.py, который
# сам импортирует onboarding.py — с любым из этих двух модулей внутри
# пакета Handlers получился бы циклический импорт.

STATUS_LABELS = {
    "new": "🆕 Новый",
    "notified": "📨 Уведомлён",
    "phone_requested": "📞 Запрошен телефон",
    "phone_received": "✅ Телефон получен",
    "closed": "🔒 Закрыт",
}


class RefreshStatsCallback(CallbackData, prefix="mystats_refresh"):
    pass


class EditDirectTextCallback(CallbackData, prefix="edit_direct_text"):
    pass


class EditThankYouTextCallback(CallbackData, prefix="edit_thank_you_text"):
    pass


class AddMediaCallback(CallbackData, prefix="add_media"):
    pass


async def build_manager_home_view(db: Database, manager_chat_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    client = await db.get_client_by_manager_chat_id(manager_chat_id)
    if client is None:
        return None

    active_count = await db.count_active_media(client["ig_business_id"])
    if active_count == 0:
        text = (
            f"👋 <b>{html.escape(client['name'])}</b> подключён к LeadArmor!\n\n"
            "Остался один шаг: добавь пост, видео или рекламный креатив, под которым "
            "бот должен ловить лиды. Пока ничего не запущено — бот не реагирует ни на "
            "один комментарий, даже если там есть 'цена' или '+'."
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="➕ Добавить пост", callback_data=AddMediaCallback().pack())
        ]])
        return text, keyboard

    stats = await db.get_leads_stats_for_manager(manager_chat_id)
    recent = await db.get_recent_leads_for_manager(manager_chat_id, limit=5)

    lines = [
        f"📊 <b>Статистика {html.escape(client['name'])}</b>\n",
        f"🟢 Запущенных постов: <b>{active_count}</b>",
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

    keyboard_rows: list[list[InlineKeyboardButton]] = []
    keyboard_rows.append([
        InlineKeyboardButton(text="➕ Добавить пост", callback_data=AddMediaCallback().pack())
    ])
    if client["google_sheet_id"]:
        keyboard_rows.append([
            InlineKeyboardButton(
                text="📊 Готовые к покупке (таблица)",
                url=f"https://docs.google.com/spreadsheets/d/{client['google_sheet_id']}/edit",
            )
        ])

    keyboard_rows.append([
        InlineKeyboardButton(text="⚙️ Настроить Direct-ответ", callback_data=EditDirectTextCallback().pack())
    ])
    keyboard_rows.append([
        InlineKeyboardButton(text="🙏 Настроить Thank-you текст", callback_data=EditThankYouTextCallback().pack())
    ])
    keyboard_rows.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data=RefreshStatsCallback().pack())
    ])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard_rows)
