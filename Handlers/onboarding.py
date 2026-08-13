from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from API.instagram_oauth import build_authorize_url
from DB.database import Database
from config import settings
from manager_views import build_manager_home_view

router = Router(name="onboarding")

OWNER_WELCOME = (
    "👋 Привет! Это <b>LeadArmor Bot</b> — панель владельца.\n\n"
    "Доступные команды:\n"
    "/stats — статистика лидов (органика/таргет)\n"
    "/clients — список всех клиентов, кнопки вкл/выкл и ссылка на Google Sheets\n"
    "/recent — последние 10 лидов по всем клиентам\n"
    "/billing ig_business_id — статус подписки и триала конкретного клиента\n"
    "/add_client Название|ig_business_id|page_access_token|manager_chat_id — подключить нового клиента\n"
    "/extend_subscription ig_business_id дни — продлить оплаченную подписку"
)

GUEST_WELCOME = (
    "👋 Привет! Я <b>LeadArmor Bot</b> — защищаю и автоматически перехватываю лиды "
    "из комментариев под Instagram-рекламой для бизнеса.\n\n"
    "Подключи свой Instagram и получи {trial_days}-дневный бесплатный триал:"
)


def _connect_instagram_keyboard(tg_chat_id: int) -> InlineKeyboardMarkup:
    url = build_authorize_url(tg_chat_id)
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔗 Подключить Instagram", url=url)]])


@router.message(CommandStart())
async def start_handler(message: Message, db: Database) -> None:
    if message.from_user is None:
        return

    if message.from_user.id == settings.MANAGER_CHAT_ID:
        await message.answer(OWNER_WELCOME)
        return

    home_view = await build_manager_home_view(db, message.from_user.id)
    if home_view is not None:
        text, keyboard = home_view
        await message.answer(text, reply_markup=keyboard)
        return

    if not settings.META_OAUTH_REDIRECT_URI or not settings.META_INSTAGRAM_APP_ID:
        await message.answer(
            "👋 Привет! Я <b>LeadArmor Bot</b>.\n\n"
            "Подключение Instagram сейчас недоступно — сервис на техобслуживании, "
            "попробуй чуть позже."
        )
        return

    await message.answer(
        GUEST_WELCOME.format(trial_days=settings.TRIAL_DAYS),
        reply_markup=_connect_instagram_keyboard(message.from_user.id),
    )


@router.message(Command("connect_instagram"))
async def connect_instagram_handler(message: Message) -> None:
    if message.from_user is None:
        return

    if not settings.META_OAUTH_REDIRECT_URI or not settings.META_INSTAGRAM_APP_ID:
        await message.answer(
            "Подключение Instagram временно недоступно — не настроены "
            "META_OAUTH_REDIRECT_URI/META_INSTAGRAM_APP_ID на сервере."
        )
        return

    await message.answer(
        "Нажми кнопку ниже, войди в свой Instagram-аккаунт и разреши доступ. "
        f"Я автоматически подключу его к LeadArmor и запущу {settings.TRIAL_DAYS}-дневный триал.",
        reply_markup=_connect_instagram_keyboard(message.from_user.id),
    )
