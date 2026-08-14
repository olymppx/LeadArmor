from __future__ import annotations

import hashlib
import hmac
import logging
from urllib.parse import urlencode

import aiohttp
from aiogram import Bot
from aiohttp import web

from API.sheets import create_client_spreadsheet
from DB.database import Database
from config import settings
from manager_views import build_manager_home_view

logger = logging.getLogger(__name__)

INSTAGRAM_AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
INSTAGRAM_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
# Без версии в пути GET и POST оба падают с одинаковой "Unsupported request"
# (см. meta_api.py — там graph.instagram.com везде используется с версией).
GRAPH_INSTAGRAM_BASE = "https://graph.instagram.com/v25.0"

OAUTH_SCOPES = ",".join(settings.REQUIRED_META_PERMISSIONS)


def sign_state(tg_chat_id: int) -> str:
    signature = hmac.new(
        settings.META_APP_SECRET.encode("utf-8"),
        str(tg_chat_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{tg_chat_id}.{signature}"


def verify_state(state: str) -> int | None:
    try:
        tg_chat_id_raw, signature = state.split(".", 1)
    except ValueError:
        return None

    if not tg_chat_id_raw.lstrip("-").isdigit():
        return None

    expected = hmac.new(
        settings.META_APP_SECRET.encode("utf-8"),
        tg_chat_id_raw.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, signature):
        return None

    return int(tg_chat_id_raw)


def build_authorize_url(tg_chat_id: int) -> str:
    params = {
        "enable_fb_login": "0",
        "force_authentication": "1",
        "client_id": settings.META_INSTAGRAM_APP_ID,
        "redirect_uri": settings.META_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "state": sign_state(tg_chat_id),
    }
    return f"{INSTAGRAM_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_token(session: aiohttp.ClientSession, code: str) -> dict | None:
    data = {
        "client_id": settings.META_INSTAGRAM_APP_ID,
        "client_secret": settings.META_INSTAGRAM_APP_SECRET,
        "grant_type": "authorization_code",
        "redirect_uri": settings.META_OAUTH_REDIRECT_URI,
        "code": code,
    }
    async with session.post(INSTAGRAM_TOKEN_URL, data=data) as resp:
        payload = await resp.json(content_type=None)
        if resp.status != 200:
            logger.error("Обмен code на токен не удался (HTTP %s): %s", resp.status, payload)
            return None
        entries = payload.get("data") or [payload]
        if not entries or "access_token" not in entries[0]:
            logger.error("Некорректный ответ при обмене code на токен: %s", payload)
            return None
        # Диагностика: префикс токена (не сам токен) выдаёт, какого "сорта"
        # access_token реально пришёл, а expires_in — нужен ли вообще обмен
        # на долгоживущий (у Instagram Login токен часто уже 60-дневный).
        entry = entries[0]
        logger.info(
            "Токен получен: префикс=%s..., user_id=%s, expires_in=%s, permissions=%s",
            entry["access_token"][:6], entry.get("user_id"),
            entry.get("expires_in"), entry.get("permissions"),
        )
        return entry


async def exchange_for_long_lived_token(session: aiohttp.ClientSession, short_lived_token: str) -> str:
    """Пытается обменять токен на 60-дневный. Если Meta отказывает — возвращает
    исходный токен, а не None.

    Причина: эмпирически проверено curl'ом, что эндпоинт ig_exchange_token жив
    и принимает GET (с мусорным токеном отвечает "Failed to decrypt"), но
    именно на реальный токен Instagram Business Login отвечает "Unsupported
    request". То есть Meta расшифровывает токен и отказывает конкретно ему —
    ig_exchange_token относится к старому Basic Display API. Ронять из-за
    этого весь онбординг нельзя: сам токен рабочий, им можно пользоваться.
    """
    params = {
        "grant_type": "ig_exchange_token",
        "client_secret": settings.META_INSTAGRAM_APP_SECRET,
        "access_token": short_lived_token,
    }

    async with session.get(f"{GRAPH_INSTAGRAM_BASE}/access_token", params=params) as resp:
        payload = await resp.json(content_type=None)
        if resp.status == 200 and "access_token" in payload:
            logger.info("Long-lived токен получен (expires_in=%s)", payload.get("expires_in"))
            return payload["access_token"]

    logger.warning(
        "Обмен на long-lived токен отклонён Meta (HTTP %s): %s — используем исходный токен",
        resp.status, payload,
    )
    return short_lived_token


async def fetch_ig_profile(session: aiohttp.ClientSession, access_token: str) -> dict | None:
    """Забирает профиль подключаемого аккаунта.

    Пробует несколько вариантов эндпоинта: Meta развела Instagram Basic Display
    и Instagram API with Instagram Login по разным путям/полям, а какой из них
    примет конкретный токен — эмпирический вопрос (проверено curl'ом: сам путь
    жив, но реальный токен на части вариантов получает "Unsupported request").
    Первый успешный ответ и используем.
    """
    attempts = [
        ("https://graph.instagram.com/me", "user_id,username"),
        ("https://graph.instagram.com/me", "id,username"),
        (f"{GRAPH_INSTAGRAM_BASE}/me", "user_id,username"),
        ("https://graph.facebook.com/v25.0/me", "id,username"),
    ]

    for url, fields in attempts:
        params = {"fields": fields, "access_token": access_token}
        async with session.get(url, params=params) as resp:
            payload = await resp.json(content_type=None)
            if resp.status == 200:
                # Instagram Login возвращает user_id, Basic Display — id.
                account_id = payload.get("user_id") or payload.get("id")
                if account_id:
                    logger.info("Профиль получен через %s (fields=%s): %s", url, fields, payload)
                    return {"id": str(account_id), "username": payload.get("username")}
            logger.warning(
                "Профиль не получен через %s (fields=%s), HTTP %s: %s", url, fields, resp.status, payload
            )

    logger.error("Ни один вариант эндпоинта профиля не сработал")
    return None


async def oauth_callback_handler(request: web.Request) -> web.Response:
    error = request.query.get("error_description") or request.query.get("error")
    if error:
        logger.warning("Instagram OAuth отклонён пользователем: %s", error)
        return web.Response(
            text="Подключение отменено. Вернитесь в Telegram и запросите новую ссылку через /connect_instagram.",
            status=200,
        )

    code = request.query.get("code")
    state = request.query.get("state")
    if not code or not state:
        return web.Response(text="Некорректный запрос: отсутствует code или state", status=400)

    tg_chat_id = verify_state(state)
    if tg_chat_id is None:
        logger.warning("Instagram OAuth: невалидная подпись state=%s", state)
        return web.Response(
            text="Ссылка недействительна или устарела. Запросите новую через /connect_instagram.",
            status=400,
        )

    db: Database = request.app["db"]
    bot: Bot = request.app["bot"]
    session: aiohttp.ClientSession = request.app["http_session"]

    token_entry = await exchange_code_for_token(session, code)
    if token_entry is None:
        await bot.send_message(
            tg_chat_id,
            "❌ Не удалось подключить Instagram — сбой при обмене кода на токен. Попробуйте /connect_instagram ещё раз.",
        )
        return web.Response(text="Ошибка обмена токена", status=502)

    long_lived_token = await exchange_for_long_lived_token(session, token_entry["access_token"])

    profile = await fetch_ig_profile(session, long_lived_token)
    if profile is None:
        await bot.send_message(tg_chat_id, "❌ Не удалось получить данные аккаунта Instagram.")
        return web.Response(text="Профиль не получен", status=502)

    ig_business_id = profile["id"]
    username = profile.get("username", ig_business_id)

    await db.upsert_client(
        name=f"@{username}",
        ig_business_id=ig_business_id,
        page_access_token=long_lived_token,
        manager_chat_id=tg_chat_id,
    )

    sheet_id = await create_client_spreadsheet(f"@{username}")
    if sheet_id:
        await db.set_client_sheet_id(ig_business_id, sheet_id)

    home_view = await build_manager_home_view(db, tg_chat_id)
    if home_view is not None:
        text, keyboard = home_view
        await bot.send_message(tg_chat_id, text, reply_markup=keyboard)
    else:
        await bot.send_message(tg_chat_id, f"✅ Instagram-аккаунт @{username} подключён к LeadArmor!")

    return web.Response(text=f"Готово! Аккаунт @{username} подключён. Вернитесь в Telegram.", status=200)
