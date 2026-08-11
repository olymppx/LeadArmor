from __future__ import annotations

import json
import logging

import aiohttp
from aiogram import Bot
from aiohttp import web

from API import meta_api
from API.google_oauth import google_oauth_callback_handler
from API.instagram_oauth import oauth_callback_handler
from API.legal import privacy_policy_handler
from DB.database import Database
from Handlers.user_direct import process_direct_message
from config import settings
from DB import database

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhook/meta"

async def verify_handler(request: web.Request) -> web.Response:
    mode = request.query.get("hub.mode")
    token = request.query.get("hub.verify_token")
    challenge = request.query.get("hub.challenge")
    if mode == 'subscribe' and token == settings.META_VERIFY_TOKEN:
        logger.info("WebHook потвержден")
        return  web.Response(text=challenge or "", status=200)


    logger.warning("Провалена верификация вебхука (mode=%s)", mode)
    return web.Response(status=403)

async def events_handler(request: web.Request) -> web.Response:
    raw_data = await request.read()
    signature_256 = request.headers.get("X-Hub-Signature-256")
    signature_1 = request.headers.get("X-Hub-Signature")
    logger.info(
        "Webhook POST получен: %s байт, X-Hub-Signature-256=%s, X-Hub-Signature=%s",
        len(raw_data), bool(signature_256), bool(signature_1),
    )

    if not meta_api.verify_signature(raw_data, signature_256, signature_1):
        logger.warning("Невалидная подпись вебхука — запрос отклонен")
        return web.Response(status=403)

    payload = json.loads(raw_data)
    logger.info(
        "Webhook payload: object=%s, entries=%s",
        payload.get("object"), len(payload.get("entry", [])),
    )

    if payload.get("object") != "instagram":
        return web.Response(status=200)

    db: database.Database = request.app["db"]
    session: aiohttp.ClientSession = request.app["http_session"]
    bot: Bot = request.app["bot"]

    for entry in payload.get("entry", []):
        ig_business_id = entry.get("id")
        changes = entry.get("changes", [])
        messaging = entry.get("messaging", [])
        logger.info(
            "Entry ig_business_id=%s: %s changes (%s), %s messaging событий",
            ig_business_id, len(changes), [c.get("field") for c in changes], len(messaging),
        )

        for change in changes:
            if change.get("field") != "comments":
                continue
            try:
                await proccess_comment(db, session, ig_business_id, change.get("value", {}))
            except Exception:
                # Не роняем всю обработку батча из-за одного плохого события —
                # логируем и идем дальше. Meta может повторить доставку, а наши
                # add_lead/hide_comment идемпотентны, так что это безопасно.
                logger.exception("Ошибка обработки комментария: %s", change)

        for messaging_event in messaging:
            try:
                await process_direct_message(db, session, bot, ig_business_id, messaging_event)
            except Exception:
                logger.exception("Ошибка обработки сообщения Директа: %s", messaging_event)

    return web.Response(status=200)


async def proccess_comment(db: database.Database,
                        session: aiohttp.ClientSession,
                        ig_business_id: str | None,
                        value: dict,
                        ) -> None :
    comment_id: str | None = value.get("id")
    comment_text: str = value.get("text", "")
    media = value.get("media", {})
    media_id: str | None = media.get("id")
    media_product_type: str | None = media.get("media_product_type")
    from_user = value.get("from", {})
    ig_user_id: str | None = from_user.get("id")
    ig_username: str | None = from_user.get("username")

    logger.info(
        "Комментарий получен: comment_id=%s, media_id=%s, ig_user_id=%s, text=%r",
        comment_id, media_id, ig_user_id, comment_text,
    )

    if not comment_id or not media_id or not ig_user_id or not ig_business_id:
        logger.warning("Неполный payload комментария, пропускаем: %s", value)
        return

    if ig_user_id == ig_business_id:
        logger.info("Комментарий от самого бизнес-аккаунта — пропускаем")
        return

    text_lower = comment_text.lower()
    if not any(keyword.lower() in text_lower for keyword in settings.LEAD_KEYWORDS):
        logger.info("Нет триггер-слова в комментарии %r — пропускаем", comment_text)
        return

    client = await db.get_client_by_id(ig_business_id)
    if client is None:
        logger.warning(
            "Аккаунт %s не зарегистрирован, деактивирован или истёк триал/подписка — пропускаем",
            ig_business_id,
        )
        return

    post_type = meta_api.classify_post_type(media_product_type)
    lead_id = await db.add_lead(
        client_id=client["id"],
        ig_comment_id = comment_id,
        ig_media_id=media_id,
        ig_user_id=ig_user_id,
        ig_username=ig_username,
        comment_text=comment_text,
        post_type=post_type,
    )
    logger.info("Новый лид #%s (%s) от @%s: %r", lead_id, post_type, ig_username, comment_text)

    # Комментарий скрываем ТОЛЬКО у рекламы — органику трогать нельзя,
    # она держит вовлечённость поста, скрывать её незачем и вредно.
    if post_type == "ad":
        is_hidden = await meta_api.hide_comment(session, comment_id, client["page_access_token"])
        if is_hidden:
            await db.mark_comment_removed(lead_id)

    # Private reply с запросом телефона — для ЛЮБОГО лида, независимо от post_type.
    # Никаких уведомлений менеджеру и записей в Sheets на этом этапе — только
    # когда клиент реально пришлёт номер (см. Handlers/user_direct.py).
    reply_sent = await meta_api.send_private_reply(
        session, ig_business_id, comment_id, client["page_access_token"], username=ig_username,
    )
    if reply_sent:
        await db.update_lead_status(lead_id, "phone_requested")

def create_app(db: Database, bot : Bot) -> web.Application:
    app = web.Application()
    app["db"] = db
    app["bot"] = bot
    app["http_session"] = aiohttp.ClientSession()

    app.router.add_get(WEBHOOK_PATH, verify_handler)
    app.router.add_post(WEBHOOK_PATH, events_handler)
    app.router.add_get("/oauth/instagram/callback", oauth_callback_handler)
    app.router.add_get("/oauth/google/callback", google_oauth_callback_handler)
    app.router.add_get("/legal/privacy", privacy_policy_handler)

    async def _cleanup(app: web.Application) -> None:
        await app["http_session"].close()

    app.on_cleanup.append(_cleanup)
    return app
