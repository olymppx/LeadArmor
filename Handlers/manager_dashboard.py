from __future__ import annotations

import html

from aiogram import Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from DB.database import Database

router = Router(name="manager_dashboard")

STATUS_LABELS = {
    "new": "🆕 Новый",
    "notified": "📨 Уведомлён",
    "phone_requested": "📞 Запрошен телефон",
    "phone_received": "✅ Телефон получен",
    "closed": "🔒 Закрыт",
}


class RefreshStatsCallback(CallbackData, prefix="mystats_refresh"):
    pass


async def _build_mystats_view(db: Database, manager_chat_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    client = await db.get_client_by_manager_chat_id(manager_chat_id)
    if client is None:
        return None

    stats = await db.get_leads_stats_for_manager(manager_chat_id)
    recent = await db.get_recent_leads_for_manager(manager_chat_id, limit=5)

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

    keyboard_rows: list[list[InlineKeyboardButton]] = []
    if client["google_sheet_id"]:
        keyboard_rows.append([
            InlineKeyboardButton(
                text="📊 Готовые к покупке (таблица)",
                url=f"https://docs.google.com/spreadsheets/d/{client['google_sheet_id']}/edit",
            )
        ])

    keyboard_rows.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data=RefreshStatsCallback().pack())
    ])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


@router.message(Command("mystats"))
async def my_stats_handler(message: Message, db: Database) -> None:
    if message.from_user is None:
        return

    view = await _build_mystats_view(db, message.from_user.id)
    if view is None:
        await message.answer(
            "Эта команда для менеджеров подключённых аккаунтов. "
            "Если это ошибка — свяжитесь с владельцем бота."
        )
        return

    text, keyboard = view
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(RefreshStatsCallback.filter())
async def refresh_stats_handler(callback: CallbackQuery, db: Database) -> None:
    if callback.from_user is None:
        return

    view = await _build_mystats_view(db, callback.from_user.id)
    if view is None:
        await callback.answer("Доступ утерян", show_alert=True)
        return

    text, keyboard = view
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer("Обновлено")
