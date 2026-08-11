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


def _build_lead_alert_text(username: str | None, comment_text: str, post_type: str, status_line: str = "") -> str:
    safe_username = html.escape(username) if username else "без username"
    safe_comment = html.escape(comment_text)
    if post_type == "ad":
        header = "🚨 <b>ПЕРЕХВАТ ТАРГЕТ-ЛИДА!</b>"
        source_line = "оставил комментарий под рекламой"
        action_line = (
            "Скрипт мгновенно скрыл коммент от конкурентов и отправил "
            "private reply в Директ с запросом телефона!"
        )
    else:
        header = "🌿 <b>НОВЫЙ ЛИД (органика)!</b>"
        source_line = "оставил комментарий"
        action_line = (
            "Комментарий остался виден (это не реклама, скрывать не нужно), "
            "но private reply с запросом телефона уже отправлен в Директ!"
        )
    text = f"{header}\n\nПользователь @{safe_username} {source_line}:\n«{safe_comment}»\n\n{action_line}"
    if status_line:
        text += f"\n\n{status_line}"
    return text


async def notify_manager_about_ad_lead(
    bot: Bot,
    manager_chat_id: int,
    username: str | None,
    comment_text: str,
    lead_id: int,
    post_type: str = "ad",
) -> None:
    text = _build_lead_alert_text(username, comment_text, post_type)
    await bot.send_message(
        chat_id=manager_chat_id,
        text=text,
        reply_markup=_build_lead_keyboard(lead_id),
    )
    logger.info("Менеджер %s уведомлён о лиде #%s (%s)", manager_chat_id, lead_id, post_type)


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
    status_line = f"Статус: {status_label} | Телефон: {phone_label}"

    text = _build_lead_alert_text(lead["ig_username"], lead["comment_text"], lead["post_type"], status_line)

    await callback.message.edit_text(text, reply_markup=_build_lead_keyboard(lead["id"]))
    await callback.answer("Обновлено")
