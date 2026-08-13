from __future__ import annotations

import html

from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from API.meta_api import THANK_YOU_TEXT, build_private_reply_text
from DB.database import Database

router = Router(name="manager_dashboard")


class DirectTextStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_thank_you_text = State()

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
        InlineKeyboardButton(text="⚙️ Настроить Direct-ответ", callback_data=EditDirectTextCallback().pack())
    ])
    keyboard_rows.append([
        InlineKeyboardButton(text="🙏 Настроить Thank-you текст", callback_data=EditThankYouTextCallback().pack())
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


@router.callback_query(EditDirectTextCallback.filter())
async def edit_direct_text_handler(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    if callback.from_user is None:
        return

    client = await db.get_client_by_manager_chat_id(callback.from_user.id)
    if client is None:
        await callback.answer("Доступ утерян", show_alert=True)
        return

    current_text = client["custom_direct_text"] or build_private_reply_text(None)

    await state.update_data(ig_business_id=client["ig_business_id"])
    await state.set_state(DirectTextStates.waiting_for_text)

    await callback.message.answer(
        "✏️ <b>Текущий текст Direct-ответа:</b>\n\n"
        f"<code>{html.escape(current_text)}</code>\n\n"
        "Пришли новый текст. Обязательно включи тег <code>{username}</code> — "
        "на его место подставится имя клиента из Instagram.\n\n"
        "Для отмены — /cancel"
    )
    await callback.answer()


@router.message(
    StateFilter(DirectTextStates.waiting_for_text, DirectTextStates.waiting_for_thank_you_text),
    Command("cancel"),
)
async def cancel_direct_text_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено, текст не изменён.")


@router.message(DirectTextStates.waiting_for_text)
async def receive_direct_text_handler(message: Message, state: FSMContext, db: Database) -> None:
    if message.from_user is None:
        return

    new_text = (message.text or "").strip()

    if "{username}" not in new_text:
        await message.answer(
            "⚠️ В тексте должен быть тег <code>{username}</code> — без него бот не сможет "
            "подставить имя клиента. Пришли текст ещё раз или /cancel для отмены."
        )
        return

    updated = await db.update_custom_direct_text(message.from_user.id, new_text)
    await state.clear()

    if not updated:
        await message.answer("Доступ утерян, изменения не сохранены.")
        return

    await message.answer(
        "Muvaffaqiyatli! Янги Direct-матни лазерным бетоном запечатан в базу данных! 🚀🛡️"
    )


@router.callback_query(EditThankYouTextCallback.filter())
async def edit_thank_you_text_handler(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    if callback.from_user is None:
        return

    client = await db.get_client_by_manager_chat_id(callback.from_user.id)
    if client is None:
        await callback.answer("Доступ утерян", show_alert=True)
        return

    current_text = client["custom_thank_you_text"] or THANK_YOU_TEXT

    await state.set_state(DirectTextStates.waiting_for_thank_you_text)

    await callback.message.answer(
        "✏️ <b>Текущий Thank-you текст (после получения номера):</b>\n\n"
        f"<code>{html.escape(current_text)}</code>\n\n"
        "Пришли новый текст. Тег <code>{username}</code> необязателен — можно "
        "написать просто текст без имени клиента.\n\n"
        "Для отмены — /cancel"
    )
    await callback.answer()


@router.message(DirectTextStates.waiting_for_thank_you_text)
async def receive_thank_you_text_handler(message: Message, state: FSMContext, db: Database) -> None:
    if message.from_user is None:
        return

    new_text = (message.text or "").strip()
    if not new_text:
        await message.answer("Текст не может быть пустым. Пришли текст ещё раз или /cancel для отмены.")
        return

    updated = await db.update_custom_thank_you_text(message.from_user.id, new_text)
    await state.clear()

    if not updated:
        await message.answer("Доступ утерян, изменения не сохранены.")
        return

    await message.answer(
        "Muvaffaqiyatli! Янги Thank-you матни лазерным бетоном запечатан в базу данных! 🚀🛡️"
    )
