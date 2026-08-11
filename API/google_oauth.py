from __future__ import annotations

import logging
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

from config import settings

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/drive.file"


def build_google_authorize_url() -> str:
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def google_oauth_callback_handler(request: web.Request) -> web.Response:
    error = request.query.get("error")
    if error:
        return web.Response(text=f"Авторизация отменена: {error}", status=200)

    code = request.query.get("code")
    if not code:
        return web.Response(text="Нет кода авторизации в запросе", status=400)

    data = {
        "code": code,
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
        "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
        "grant_type": "authorization_code",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(GOOGLE_TOKEN_URL, data=data) as resp:
            payload = await resp.json()
            if resp.status != 200:
                logger.error("Google OAuth: обмен кода на токен не удался: %s", payload)
                return web.Response(text=f"Ошибка обмена токена: {payload}", status=502)

    bot = request.app["bot"]
    refresh_token = payload.get("refresh_token")

    if refresh_token:
        await bot.send_message(
            settings.MANAGER_CHAT_ID,
            "✅ Google-авторизация прошла успешно!\n\n"
            "Скопируй это значение и пришли его в чат — я сохраню в <code>.env</code> "
            f"как <code>GOOGLE_OAUTH_REFRESH_TOKEN</code>:\n\n<code>{refresh_token}</code>",
        )
        return web.Response(text="Готово! Refresh token отправлен тебе в Telegram.", status=200)

    await bot.send_message(
        settings.MANAGER_CHAT_ID,
        "⚠️ Google вернул токен без refresh_token — обычно это значит, что доступ "
        "уже выдавался раньше. Зайди на https://myaccount.google.com/permissions, "
        "отзови доступ для приложения LeadArmor, и повтори /connect_google_sheets.",
    )
    return web.Response(text="Refresh token не получен — смотри Telegram", status=200)
