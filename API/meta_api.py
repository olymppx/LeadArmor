from __future__ import annotations

import hashlib
import hmac
import logging
import re

import aiohttp

from config import  settings
logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v25.0"
# graph.instagram.com — токены IGAA... (Instagram Business Login) распознаются
# только этим хостом, не graph.facebook.com (тот для Facebook Page токенов).
GRAPH_API_BASE = f"https://graph.instagram.com/{GRAPH_API_VERSION}"

CANDIDATE_SECRETS = [settings.META_APP_SECRET, settings.META_INSTAGRAM_APP_SECRET]


def verify_signature(
    raw_body: bytes,
    signature_header_256: str | None,
    signature_header_1: str | None = None,
) -> bool:
    for header_value in (signature_header_256, signature_header_1):
        if not header_value:
            continue

        if header_value.startswith("sha256="):
            expected = header_value.removeprefix("sha256=")
            for secret in CANDIDATE_SECRETS:
                if not secret:
                    continue
                computed = hmac.new(
                    key=secret.encode("utf-8"),
                    msg=raw_body,
                    digestmod=hashlib.sha256,
                ).hexdigest()
                if hmac.compare_digest(computed, expected):
                    return True

        elif header_value.startswith("sha1="):
            expected = header_value.removeprefix("sha1=")
            for secret in CANDIDATE_SECRETS:
                if not secret:
                    continue
                computed = hmac.new(
                    key=secret.encode("utf-8"),
                    msg=raw_body,
                    digestmod=hashlib.sha1,
                ).hexdigest()
                if hmac.compare_digest(computed, expected):
                    return True

    return False

def classify_post_type(media_type: str | None) -> str:
    return  'ad' if media_type == 'AD' else 'organic'

async def fetch_media_title(session: aiohttp.ClientSession, media_id: str, access_token: str) -> str | None:
    # Человекочитаемое название поста для списков/карточек в Telegram — без
    # этого юзер видит только голый media_id, который ничего не говорит.
    url = f"{GRAPH_API_BASE}/{media_id}"
    params = {"fields": "caption", "access_token": access_token}
    async with session.get(url, params=params) as resp:
        data = await resp.json()
        if resp.status != 200:
            logger.warning("Не удалось получить caption для media_id=%s (HTTP %s): %s", media_id, resp.status, data)
            return None
        caption = data.get("caption")
        return caption.strip() if caption else None

async def hide_comment(session: aiohttp.ClientSession, comment_id: str, access_token : str) -> bool:
    url = f"{GRAPH_API_BASE}/{comment_id}"
    params = {"hidden" : "true", "access_token" : access_token}
    async with session.post(url, params=params) as resp:
        data = await resp.json()
        if resp.status == 200:
            logger.info("Комментарий %s скрыт: %s", comment_id, data)
            return True
        logger.error("Не удалось скрыть комментарий %s (HTTP %s): %s", comment_id, resp.status, data)
        return False

SHORTCODE_PATTERN = re.compile(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)")


async def _verify_owned_media_id(
    session: aiohttp.ClientSession, access_token: str, media_id: str
) -> str | None:
    # Прямой числовой ID подтверждаем через Graph API тем же токеном, что и
    # владелец аккаунта — если media чужой, Meta сама откажет по правам доступа.
    url = f"{GRAPH_API_BASE}/{media_id}"
    params = {"fields": "id", "access_token": access_token}
    async with session.get(url, params=params) as resp:
        data = await resp.json()
        if resp.status == 200 and data.get("id"):
            return data["id"]
        logger.warning("media_id %s не подтверждён Graph API (HTTP %s): %s", media_id, resp.status, data)
        return None


async def _find_media_id_by_shortcode(
    session: aiohttp.ClientSession, ig_business_id: str, access_token: str, shortcode: str
) -> str | None:
    # Shortcode из ссылки (instagram.com/p/XXXX/) — это НЕ Graph API media_id,
    # конвертера между ними не существует. Единственный надёжный способ —
    # пройтись по собственным медиа аккаунта и сопоставить по permalink.
    url = f"{GRAPH_API_BASE}/{ig_business_id}/media"
    params: dict[str, str] = {"fields": "id,permalink", "access_token": access_token, "limit": "50"}

    for _ in range(5):  # максимум ~250 последних постов, защита от бесконечной пагинации
        async with session.get(url, params=params) as resp:
            data = await resp.json()
            if resp.status != 200:
                logger.error(
                    "Не удалось получить список медиа %s (HTTP %s): %s", ig_business_id, resp.status, data
                )
                return None

            for item in data.get("data", []):
                if shortcode in item.get("permalink", ""):
                    return item["id"]

            next_url = data.get("paging", {}).get("next")
            if not next_url:
                return None
            url, params = next_url, {}

    return None


async def resolve_owned_media_id(
    session: aiohttp.ClientSession,
    ig_business_id: str,
    access_token: str,
    user_input: str,
) -> str | None:
    user_input = user_input.strip()

    shortcode_match = SHORTCODE_PATTERN.search(user_input)
    if shortcode_match is not None:
        return await _find_media_id_by_shortcode(session, ig_business_id, access_token, shortcode_match.group(1))

    if user_input.isdigit():
        return await _verify_owned_media_id(session, access_token, user_input)

    return None


def build_private_reply_text(username: str | None) -> str:
    name_part = f"@{username}" if username else "mijoz"
    return (
        f"Salam, {name_part}!\n"
        "Agar maxsulotimizni sotib olmoqchi bo'lsangiz nomeringizni qoldiring "
        "va biz sizga bog'lanamiz \U0001F680\U0001F6E1"
    )

def resolve_direct_reply_text(custom_text: str | None, username: str | None) -> str:
    if not custom_text:
        return build_private_reply_text(username)

    # .replace, а не .format() — custom_text вводит бизнес-клиент через Telegram.
    # str.format() на чужом вводе — format-string injection: {username.__class__...}
    # улетит в реальный Instagram Direct. replace() не резолвит атрибуты, безопасно.
    name_part = f"@{username}" if username else "mijoz"
    return custom_text.replace("{username}", name_part)


QUICK_REPLY_PAYLOAD = "SEND_PHONE_NUMBER"
QUICK_REPLY_TITLE = "\U0001F4DE Telefon raqamni yuborish"

THANK_YOU_TEXT = (
    "Rahmat! Sizning so'rovingiz qabul qilindi va menedjerimiz "
    "siz bilan tez orada bog'lanadi!\U0001F680\U0001F6E1"
)


def resolve_thank_you_text(custom_text: str | None, username: str | None) -> str:
    if not custom_text:
        return THANK_YOU_TEXT

    # Тег {username} необязателен здесь (в отличие от direct-reply) — это
    # финальное сообщение "спасибо", многим клиентам достаточно текста без имени.
    name_part = f"@{username}" if username else "mijoz"
    return custom_text.replace("{username}", name_part)

# Требует в Meta App Dashboard одобренных прав instagram_business_basic,
# instagram_business_manage_comments, instagram_business_manage_messages
# и подписки вебхука на поля messages и messaging_postbacks.
async def send_private_reply(
    session: aiohttp.ClientSession,
    ig_business_id: str,
    comment_id: str,
    access_token: str,
    message_text: str,
) -> bool:
    url = f"{GRAPH_API_BASE}/{ig_business_id}/messages"
    params = {"access_token": access_token}
    payload = {
        "recipient": {"comment_id": comment_id},
        "message": {
            "text": message_text,
            "quick_replies": [
                {
                    "content_type": "text",
                    "title": QUICK_REPLY_TITLE,
                    "payload": QUICK_REPLY_PAYLOAD,
                }
            ],
        },
    }
    async with session.post(url, params=params, json=payload) as resp:
        data = await resp.json()
        if resp.status == 200:
            logger.info("Private reply отправлен на комментарий %s: %s", comment_id, data)
            return True
        logger.error(
            "Не удалось отправить private reply на комментарий %s (HTTP %s): %s. "
            "Проверьте в Meta App Dashboard права instagram_business_basic, "
            "instagram_business_manage_comments, instagram_business_manage_messages "
            "и подписку вебхука на поля messages/messaging_postbacks.",
            comment_id, resp.status, data,
        )
        return False


async def send_thank_you_message(
    session: aiohttp.ClientSession,
    ig_business_id: str,
    ig_user_id: str,
    access_token: str,
    message_text: str,
) -> bool:
    url = f"{GRAPH_API_BASE}/{ig_business_id}/messages"
    params = {"access_token": access_token}
    payload = {
        "recipient": {"id": ig_user_id},
        "message": {"text": message_text},
    }
    async with session.post(url, params=params, json=payload) as resp:
        data = await resp.json()
        if resp.status == 200:
            logger.info("Спасибо-сообщение отправлено ig_user_id=%s: %s", ig_user_id, data)
            return True
        logger.error(
            "Не удалось отправить спасибо-сообщение ig_user_id=%s (HTTP %s): %s",
            ig_user_id, resp.status, data,
        )
        return False
