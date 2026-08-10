from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from API.instagram_oauth import build_authorize_url
from DB.database import Database
from config import settings

router = Router(name="onboarding")

OWNER_WELCOME = (
    "👋 Привет! Это <b>LeadArmor Bot</b> — панель владельца.\n\n"
    "Доступные команды:\n"
    "/stats — статистика лидов (органика/таргет)\n"
    "/clients — список всех клиентов и статус подписки\n"
    "/billing ig_business_id — статус подписки и триала конкретного клиента\n"
    "/add_client Название|ig_business_id|page_access_token|manager_chat_id — подключить нового клиента\n"
    "/extend_subscription ig_business_id дни — продлить оплаченную подписку"
)

MANAGER_WELCOME = (
    "👋 Привет, {name}!\n\n"
    "Я <b>LeadArmor Bot</b> — слежу за комментариями под вашей рекламой в Instagram. "
    "Как только клиент напишет ключевое слово ('цена', 'купить', '+' и т.д.) под таргетом, я:\n\n"
    "1️⃣ Мгновенно скрою комментарий от конкурентов\n"
    "2️⃣ Запрошу номер телефона в Директе\n"
    "3️⃣ Пришлю уведомление прямо сюда с кнопкой проверки статуса лида\n\n"
    "Никаких действий от вас пока не требуется — просто ждите уведомлений."
)

GUEST_WELCOME = (
    "👋 Привет! Я <b>LeadArmor Bot</b> — защищаю и автоматически перехватываю лиды "
    "из комментариев под Instagram-рекламой для бизнеса.\n\n"
    "Хотите подключить свой Instagram и получить {trial_days}-дневный бесплатный триал? "
    "Отправьте /connect_instagram."
)


@router.message(CommandStart())
async def start_handler(message: Message, db: Database) -> None:
    if message.from_user is None:
        return

    if message.from_user.id == settings.MANAGER_CHAT_ID:
        await message.answer(OWNER_WELCOME)
        return

    client = await db.get_client_by_manager_chat_id(message.from_user.id)
    if client is not None:
        await message.answer(MANAGER_WELCOME.format(name=client["name"]))
        return

    await message.answer(GUEST_WELCOME.format(trial_days=settings.TRIAL_DAYS))


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

    url = build_authorize_url(message.from_user.id)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔗 Подключить Instagram", url=url)]]
    )
    await message.answer(
        "Нажмите кнопку ниже, войдите в свой Instagram-аккаунт и разрешите доступ. "
        f"Я автоматически подключу его к LeadArmor и запущу {settings.TRIAL_DAYS}-дневный триал.",
        reply_markup=keyboard,
    )
