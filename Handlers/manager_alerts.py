from __future__ import annotations

import html
import logging

from aiogram import Bot, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from DB.database import Database

logger = logging.getLogger(__name__)

router = Router(name="manager_alerts")

STATUS_LABELS: dict[str, str] = {
    "new": "🆕 Новый",
    "notified": "📨 Менеджер уведомлён",
    "phone_requested": "📞 Запрошен телефон",
    "phone_received": "✅ Телефон получен",
    "closed": "🔒 Закрыт",
}


class LeadStatusCallback(CallbackData, prefix="lead_status"):
    lead_id: int


def _build_lead_keyboard(lead_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Проверить статус лида",
                    callback_data=LeadStatusCallback(lead_id=lead_id).pack(),
                )
            ]
        ]
    )


async def notify_manager_about_ad_lead(
    bot: Bot,
    manager_chat_id: int,
    username: str | None,
    comment_text: str,
    lead_id: int,
) -> None:
    safe_username = html.escape(username) if username else "без username"
    safe_comment = html.escape(comment_text)
    text = (
        "🚨 <b>ПЕРЕХВАТ ТАРГЕТ-ЛИДА!</b>\n\n"
        f"Пользователь @{safe_username} оставил комментарий под рекламой:\n"
        f"«{safe_comment}»\n\n"
        "Скрипт мгновенно скрыл коммент от конкурентов и отправил "
        "private reply в Директ с запросом телефона!"
    )
    await bot.send_message(
        chat_id=manager_chat_id,
        text=text,
        reply_markup=_build_lead_keyboard(lead_id),
    )
    logger.info("Менеджер %s уведомлён о лиде #%s", manager_chat_id, lead_id)


async def notify_manager_about_phone_received(
    bot: Bot,
    manager_chat_id: int,
    username: str | None,
    phone_number: str,
) -> None:
    safe_username = html.escape(username) if username else "без username"
    text = (
        "✅ <b>НОМЕР ТЕЛЕФОНА ПОЛУЧЕН!</b>\n\n"
        f"Клиент @{safe_username} оставил телефон: <code>{phone_number}</code>.\n"
        "Лид успешно закрыт в базе данных!"
    )
    await bot.send_message(chat_id=manager_chat_id, text=text)
    logger.info("Менеджер %s уведомлён о получении телефона от @%s", manager_chat_id, username)


@router.callback_query(LeadStatusCallback.filter())
async def check_lead_status_handler(
    callback: CallbackQuery,
    callback_data: LeadStatusCallback,
    db: Database,
) -> None:
    lead = await db.get_lead_by_id(callback_data.lead_id)
    if lead is None:
        await callback.answer("Лид не найден в базе", show_alert=True)
        return

    status_label = STATUS_LABELS.get(lead["status"], lead["status"])
    phone_label = lead["phone_number"] or "Ещё не оставил"
    safe_username = html.escape(lead["ig_username"]) if lead["ig_username"] else "без username"
    safe_comment = html.escape(lead["comment_text"])

    text = (
        "🚨 <b>ПЕРЕХВАТ ТАРГЕТ-ЛИДА!</b>\n\n"
        f"Пользователь @{safe_username} оставил комментарий под рекламой:\n"
        f"«{safe_comment}»\n\n"
        "Скрипт мгновенно скрыл коммент от конкурентов и отправил "
        "private reply в Директ с запросом телефона!\n\n"
        f"Статус: {status_label} | Телефон: {phone_label}"
    )

    await callback.message.edit_text(text, reply_markup=_build_lead_keyboard(lead["id"]))
    await callback.answer("Обновлено" )
