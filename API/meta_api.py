from __future__ import annotations

import hashlib
import hmac
import logging

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

PRIVATE_REPLY_TEXT = (
    "Здравствуйте! Спасибо за проявленный интерес. "
    "Пожалуйста, оставьте ваш номер телефона прямо здесь в Директе, "
    "и наш менеджер моментально свяжется с вами в Telegram!"
)

# Требует в Meta App Dashboard одобренных прав instagram_business_basic,
# instagram_business_manage_comments, instagram_business_manage_messages
# и подписки вебхука на поля messages и messaging_postbacks.
async def send_private_reply(
    session: aiohttp.ClientSession,
    ig_business_id: str,
    comment_id: str,
    access_token: str,
) -> bool:
    url = f"{GRAPH_API_BASE}/{ig_business_id}/messages"
    params = {"access_token": access_token}
    payload = {
        "recipient": {"comment_id": comment_id},
        "message": {"text": PRIVATE_REPLY_TEXT},
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
