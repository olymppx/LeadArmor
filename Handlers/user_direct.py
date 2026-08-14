from __future__ import annotations

import logging
import re

import aiohttp
from aiogram import Bot

from API.meta_api import resolve_thank_you_text, send_direct_message
from API.sheets import append_confirmed_lead
from DB.database import Database
from Handlers.manager_alerts import notify_manager_about_phone_received

logger = logging.getLogger(__name__)

# Разделители, которые реально встречаются у живых людей: пробел, дефис,
# точка, скобки, длинное/среднее тире (мобильные клавиатуры их подставляют
# при автозамене дефиса).
PHONE_CANDIDATE_PATTERN = re.compile(r"\+?\d[\d\s\-.()–—]{6,18}\d")


def normalize_uzbek_phone(text: str) -> str | None:
    for match in PHONE_CANDIDATE_PATTERN.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))

        if digits.startswith("998") and len(digits) == 12:
            subscriber_number = digits[3:]
        elif digits.startswith("8") and len(digits) == 10:
            # Локальная привычка набора "8-90-123-45-67" вместо "+998"
            subscriber_number = digits[1:]
        elif len(digits) == 9:
            subscriber_number = digits
        else:
            continue

        if re.fullmatch(r"\d{9}", subscriber_number):
            return f"+998{subscriber_number}"

    return None


async def process_direct_message(
    db: Database,
    session: aiohttp.ClientSession,
    bot: Bot,
    ig_business_id: str | None,
    value: dict,
) -> None:
    sender = value.get("sender", {})
    ig_user_id: str | None = sender.get("id")
    message = value.get("message", {})
    text: str = message.get("text", "")

    if not ig_business_id or not ig_user_id:
        return

    if message.get("is_echo"):
        return

    if message.get("quick_reply"):
        # Клиент нажал кнопку "Telefon raqamni yuborish" — это не сам номер,
        # а подтверждение намерения его отправить. Реальный номер придёт
        # следующим сообщением и поймается обычным regex-сканированием ниже.
        logger.info("Quick reply от ig_user_id=%s: %s", ig_user_id, message["quick_reply"].get("payload"))
        return

    if not text:
        return

    phone_number = normalize_uzbek_phone(text)
    if not phone_number:
        return

    updated_lead = await db.save_lead_phone(
        ig_business_id=ig_business_id,
        ig_user_id=ig_user_id,
        phone_number=phone_number,
    )
    if updated_lead is None:
        logger.info(
            "Номер %s от ig_user_id=%s не привязан ни к одному активному лиду (client=%s)",
            phone_number, ig_user_id, ig_business_id,
        )
        return

    logger.info(
        "Лид #%s (@%s) оставил номер телефона в Директе: %s",
        updated_lead["id"], updated_lead["ig_username"], phone_number,
    )

    if updated_lead["save_to_sheets"]:
        await append_confirmed_lead(
            sheet_id=updated_lead["google_sheet_id"],
            client_name=updated_lead["client_name"],
            ig_username=updated_lead["ig_username"],
            phone_number=phone_number,
            post_type=updated_lead["post_type"],
            is_hidden=updated_lead["is_comment_removed"],
            status=updated_lead["status"],
        )
    else:
        logger.info("Лид #%s: запись в Sheets отключена для этого поста — пропускаем", updated_lead["id"])

    thank_you_text = resolve_thank_you_text(updated_lead["thank_you_text"], updated_lead["ig_username"])
    logger.info("Резолвленный thank-you текст для client_id=%s: %r", updated_lead["client_id"], thank_you_text)
    await send_direct_message(
        session=session,
        ig_business_id=ig_business_id,
        ig_user_id=ig_user_id,
        access_token=updated_lead["page_access_token"],
        message_text=thank_you_text,
    )

    await notify_manager_about_phone_received(
        bot=bot,
        manager_chat_id=updated_lead["manager_chat_id"],
        username=updated_lead["ig_username"],
        phone_number=phone_number,
    )
