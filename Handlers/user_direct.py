from __future__ import annotations

import logging
import re

from aiogram import Bot

from API.sheets import append_confirmed_lead
from DB.database import Database
from Handlers.manager_alerts import notify_manager_about_phone_received

logger = logging.getLogger(__name__)

PHONE_CANDIDATE_PATTERN = re.compile(r"[\+]?\d[\d\s\-()]{7,16}\d")


def normalize_uzbek_phone(text: str) -> str | None:
    match = PHONE_CANDIDATE_PATTERN.search(text)
    if not match:
        return None

    digits = re.sub(r"\D", "", match.group(0))

    if digits.startswith("998") and len(digits) == 12:
        subscriber_number = digits[3:]
    elif len(digits) == 9:
        subscriber_number = digits
    else:
        return None

    if not re.fullmatch(r"\d{9}", subscriber_number):
        return None

    return f"+998{subscriber_number}"


async def process_direct_message(
    db: Database,
    bot: Bot,
    ig_business_id: str | None,
    value: dict,
) -> None:
    sender = value.get("sender", {})
    ig_user_id: str | None = sender.get("id")
    message = value.get("message", {})
    text: str = message.get("text", "")

    if not ig_business_id or not ig_user_id or not text:
        return

    if message.get("is_echo"):
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

    await append_confirmed_lead(
        client_name=updated_lead["client_name"],
        ig_username=updated_lead["ig_username"],
        phone_number=phone_number,
        source=updated_lead["post_type"],
        is_hidden=updated_lead["is_comment_removed"],
        status=updated_lead["status"],
    )

    await notify_manager_about_phone_received(
        bot=bot,
        manager_chat_id=updated_lead["manager_chat_id"],
        username=updated_lead["ig_username"],
        phone_number=phone_number,
    )
