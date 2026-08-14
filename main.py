from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web

from API.web_hook import create_app
from DB.database import Database
from Handlers import routers
from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def on_startup(db: Database) -> None:
    await db.connect()
    await db.init_models()
    logger.info("PostgreSQL: подключение установлено, схема готова")


async def main() -> None:
    db = Database()
    await on_startup(db)

    bot = Bot(token=settings.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp["db"] = db

    http_session = aiohttp.ClientSession()
    dp["http_session"] = http_session

    dp.include_routers(*routers)

    app = create_app(db, bot, http_session)
    runner = web.AppRunner(app)
    await runner.setup()
    # Хостинги (Render, Koyeb, Fly) сами назначают порт через переменную PORT
    # и ждут, что процесс будет слушать именно его — иначе деплой считается
    # упавшим. Локально переменной нет, поэтому дефолт 8000 как раньше.
    port = settings.PORT
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    logger.info(
        "Webhook-сервер слушает на порту %s (%s)",
        port,
        "verify_token задан" if settings.META_VERIFY_TOKEN else "⚠️ META_VERIFY_TOKEN пуст",
    )
    logger.info(
        "Meta App Dashboard checklist — права: %s | поля вебхука: %s",
        ", ".join(settings.REQUIRED_META_PERMISSIONS),
        ", ".join(settings.REQUIRED_META_WEBHOOK_FIELDS),
    )

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await http_session.close()
        await db.disconnect()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Остановлено")
