from __future__ import annotations

import html
import logging

import aiohttp
from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from API.meta_api import THANK_YOU_TEXT, build_private_reply_text, resolve_owned_media_id
from DB.database import Database
from manager_views import (
    AddMediaCallback,
    EditDirectTextCallback,
    EditThankYouTextCallback,
    RefreshStatsCallback,
    build_manager_home_view,
)

logger = logging.getLogger(__name__)

router = Router(name="manager_dashboard")


async def _safe_answer(callback: CallbackQuery, *args, **kwargs) -> None:
    # Если long-polling у бота на секунды/минуты обрывался (сетевой сбой),
    # Telegram доставляет накопившиеся callback_query уже "протухшими" —
    # answer() на них падает с TelegramBadRequest. К этому моменту реальное
    # действие (запись в БД, следующее сообщение) уже должно было отработать
    # — эта ошибка не должна ронять весь хендлер и прятать результат от юзера.
    try:
        await callback.answer(*args, **kwargs)
    except TelegramBadRequest:
        logger.warning("callback.answer() не прошёл (устаревший callback_query) — игнорируем")


class DirectTextStates(StatesGroup):
    waiting_for_text = State()
    waiting_for_thank_you_text = State()


class MediaWizardStates(StatesGroup):
    waiting_for_media_link = State()
    waiting_for_keywords = State()
    waiting_for_reply_text = State()


class ConfigureTriggerCallback(CallbackData, prefix="cfg_trigger"):
    media_id: str


class SetTriggerTypeCallback(CallbackData, prefix="set_trigger_type"):
    media_id: str
    trigger_type: str


class ConfigureReplyTextCallback(CallbackData, prefix="cfg_reply_text"):
    media_id: str


class SkipReplyTextCallback(CallbackData, prefix="skip_reply_text"):
    media_id: str


class ToggleMediaActiveCallback(CallbackData, prefix="toggle_media"):
    media_id: str


class DeleteMediaCallback(CallbackData, prefix="delete_media"):
    media_id: str


TRIGGER_LABELS = {"all_comments": "Все комментарии", "keywords": "Ключевые слова"}


def _build_media_card(media_row) -> tuple[str, InlineKeyboardMarkup]:
    trigger_label = TRIGGER_LABELS.get(media_row["trigger_type"], media_row["trigger_type"])
    status_label = "🟢 Активен" if media_row["is_active"] else "🔴 Выключен"

    lines = [
        "📋 <b>Карточка публикации</b>\n",
        f"Пост: <code>{html.escape(media_row['media_id'])}</code>",
        f"Триггер: {trigger_label}",
    ]
    if media_row["trigger_type"] == "keywords":
        keywords = media_row["keywords_list"] or []
        lines.append(f"Ключевые слова: {', '.join(keywords) if keywords else '—'}")
    lines.append(f"Текст ответа: {'настроен ✏️' if media_row['reply_text'] else 'по умолчанию'}")
    lines.append(f"\nСтатус: {status_label}")

    toggle_text = "⏸ Остановить чат бот" if media_row["is_active"] else "🚀 Запустить чат бот"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=toggle_text,
            callback_data=ToggleMediaActiveCallback(media_id=media_row["media_id"]).pack(),
        )],
        [InlineKeyboardButton(
            text="❌ Удалить пост из панели",
            callback_data=DeleteMediaCallback(media_id=media_row["media_id"]).pack(),
        )],
    ])
    return "\n".join(lines), keyboard


@router.message(Command("mystats"))
async def my_stats_handler(message: Message, db: Database) -> None:
    if message.from_user is None:
        return

    view = await build_manager_home_view(db, message.from_user.id)
    if view is None:
        await message.answer(
            "Эта команда для менеджеров подключённых аккаунтов. "
            "Если это ошибка — свяжитесь с владельцем бота."
        )
        return

    text, keyboard = view
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(RefreshStatsCallback.filter())
async def refresh_stats_handler(callback: CallbackQuery, db: Database) -> None:
    if callback.from_user is None:
        return

    view = await build_manager_home_view(db, callback.from_user.id)
    if view is None:
        await _safe_answer(callback, "Доступ утерян", show_alert=True)
        return

    text, keyboard = view
    await callback.message.edit_text(text, reply_markup=keyboard)
    await _safe_answer(callback, "Обновлено")


@router.callback_query(EditDirectTextCallback.filter())
async def edit_direct_text_handler(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    if callback.from_user is None:
        return

    client = await db.get_client_by_manager_chat_id(callback.from_user.id)
    if client is None:
        await _safe_answer(callback, "Доступ утерян", show_alert=True)
        return

    current_text = client["custom_direct_text"] or build_private_reply_text(None)

    await state.update_data(ig_business_id=client["ig_business_id"])
    await state.set_state(DirectTextStates.waiting_for_text)

    await callback.message.answer(
        "✏️ <b>Текущий текст Direct-ответа:</b>\n\n"
        f"<code>{html.escape(current_text)}</code>\n\n"
        "Пришли новый текст. Обязательно включи тег <code>{username}</code> — "
        "на его место подставится имя клиента из Instagram.\n\n"
        "Для отмены — /cancel"
    )
    await _safe_answer(callback)


@router.message(
    StateFilter(
        DirectTextStates.waiting_for_text,
        DirectTextStates.waiting_for_thank_you_text,
        MediaWizardStates.waiting_for_media_link,
        MediaWizardStates.waiting_for_keywords,
        MediaWizardStates.waiting_for_reply_text,
    ),
    Command("cancel"),
)
async def cancel_direct_text_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено, текст не изменён.")


@router.message(DirectTextStates.waiting_for_text)
async def receive_direct_text_handler(message: Message, state: FSMContext, db: Database) -> None:
    if message.from_user is None:
        return

    new_text = (message.text or "").strip()

    if "{username}" not in new_text:
        await message.answer(
            "⚠️ В тексте должен быть тег <code>{username}</code> — без него бот не сможет "
            "подставить имя клиента. Пришли текст ещё раз или /cancel для отмены."
        )
        return

    updated = await db.update_custom_direct_text(message.from_user.id, new_text)
    await state.clear()

    if not updated:
        await message.answer("Доступ утерян, изменения не сохранены.")
        return

    await message.answer(
        "Muvaffaqiyatli! Янги Direct-матни лазерным бетоном запечатан в базу данных! 🚀🛡️"
    )


@router.callback_query(EditThankYouTextCallback.filter())
async def edit_thank_you_text_handler(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    if callback.from_user is None:
        return

    client = await db.get_client_by_manager_chat_id(callback.from_user.id)
    if client is None:
        await _safe_answer(callback, "Доступ утерян", show_alert=True)
        return

    current_text = client["custom_thank_you_text"] or THANK_YOU_TEXT

    await state.set_state(DirectTextStates.waiting_for_thank_you_text)

    await callback.message.answer(
        "✏️ <b>Текущий Thank-you текст (после получения номера):</b>\n\n"
        f"<code>{html.escape(current_text)}</code>\n\n"
        "Пришли новый текст. Тег <code>{username}</code> необязателен — можно "
        "написать просто текст без имени клиента.\n\n"
        "Для отмены — /cancel"
    )
    await _safe_answer(callback)


@router.message(DirectTextStates.waiting_for_thank_you_text)
async def receive_thank_you_text_handler(message: Message, state: FSMContext, db: Database) -> None:
    if message.from_user is None:
        return

    new_text = (message.text or "").strip()
    if not new_text:
        await message.answer("Текст не может быть пустым. Пришли текст ещё раз или /cancel для отмены.")
        return

    updated = await db.update_custom_thank_you_text(message.from_user.id, new_text)
    await state.clear()

    if not updated:
        await message.answer("Доступ утерян, изменения не сохранены.")
        return

    await message.answer(
        "Muvaffaqiyatli! Янги Thank-you матни лазерным бетоном запечатан в базу данных! 🚀🛡️"
    )


@router.callback_query(AddMediaCallback.filter())
async def add_media_handler(callback: CallbackQuery, state: FSMContext, db: Database) -> None:
    if callback.from_user is None:
        return

    client = await db.get_client_by_manager_chat_id(callback.from_user.id)
    if client is None:
        await _safe_answer(callback, "Доступ утерян", show_alert=True)
        return

    await state.set_state(MediaWizardStates.waiting_for_media_link)
    await callback.message.answer(
        "🔗 <b>Шаг 1/3.</b> Пришли ссылку на пост/видео/рекламный креатив в Instagram "
        "(например, https://www.instagram.com/p/XXXXXXXXXXX/) или прямой media_id.\n\n"
        "Для отмены — /cancel"
    )
    await _safe_answer(callback)


@router.message(MediaWizardStates.waiting_for_media_link)
async def receive_media_link_handler(
    message: Message,
    state: FSMContext,
    db: Database,
    http_session: aiohttp.ClientSession,
) -> None:
    if message.from_user is None:
        return

    raw_input = (message.text or "").strip()
    if not raw_input:
        await message.answer("Пришли ссылку или media_id, либо /cancel для отмены.")
        return

    client = await db.get_client_by_manager_chat_id(message.from_user.id)
    if client is None:
        await state.clear()
        await message.answer("Доступ утерян.")
        return

    media_id = await resolve_owned_media_id(
        http_session, client["ig_business_id"], client["page_access_token"], raw_input,
    )
    if media_id is None:
        await message.answer(
            "❌ Не нашёл такую публикацию в вашем подключённом аккаунте. Проверь ссылку "
            "(пост должен принадлежать именно этому Instagram-аккаунту) или пришли "
            "числовой media_id напрямую. /cancel для отмены."
        )
        return

    added = await db.add_media_to_monitor(client["ig_business_id"], media_id)
    await state.clear()

    if not added:
        await message.answer("⚠️ Этот пост уже добавлен на другом аккаунте.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="⚙️ Настроить условия",
            callback_data=ConfigureTriggerCallback(media_id=media_id).pack(),
        )
    ]])
    await message.answer(
        f"✅ Пост добавлен (media_id=<code>{html.escape(media_id)}</code>).\n"
        "Дальше — на какие комментарии реагировать.",
        reply_markup=keyboard,
    )


@router.callback_query(ConfigureTriggerCallback.filter())
async def configure_trigger_handler(callback: CallbackQuery, callback_data: ConfigureTriggerCallback) -> None:
    if callback.from_user is None:
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="💬 Отвечать на все комменты",
            callback_data=SetTriggerTypeCallback(media_id=callback_data.media_id, trigger_type="all_comments").pack(),
        )],
        [InlineKeyboardButton(
            text="🔑 Только на ключевые слова",
            callback_data=SetTriggerTypeCallback(media_id=callback_data.media_id, trigger_type="keywords").pack(),
        )],
    ])
    await callback.message.answer("⚙️ <b>Шаг 2/3.</b> На какие комментарии реагировать?", reply_markup=keyboard)
    await _safe_answer(callback)


@router.callback_query(SetTriggerTypeCallback.filter())
async def set_trigger_type_handler(
    callback: CallbackQuery, callback_data: SetTriggerTypeCallback, state: FSMContext, db: Database
) -> None:
    if callback.from_user is None:
        return

    if callback_data.trigger_type == "all_comments":
        ok = await db.set_media_trigger(callback.from_user.id, callback_data.media_id, "all_comments", [])
        if not ok:
            await _safe_answer(callback, "Доступ утерян", show_alert=True)
            return
        # Реальное действие — ДО _safe_answer: если callback уже "протух"
        # после разрыва соединения, юзер всё равно должен увидеть следующий шаг.
        await _prompt_reply_text_step(callback.message, callback_data.media_id)
        await _safe_answer(callback)
        return

    await state.update_data(media_id=callback_data.media_id)
    await state.set_state(MediaWizardStates.waiting_for_keywords)
    await callback.message.answer(
        "Пришли ключевые слова через запятую (например: цена, купить, +, сколько).\n\n"
        "Для отмены — /cancel"
    )
    await _safe_answer(callback)


@router.message(MediaWizardStates.waiting_for_keywords)
async def receive_keywords_handler(message: Message, state: FSMContext, db: Database) -> None:
    if message.from_user is None:
        return

    keywords = [word.strip() for word in (message.text or "").split(",") if word.strip()]
    if not keywords:
        await message.answer(
            "⚠️ Пришли хотя бы одно ключевое слово через запятую, или /cancel для отмены."
        )
        return

    data = await state.get_data()
    media_id = data.get("media_id")
    if not media_id:
        await state.clear()
        await message.answer("Что-то пошло не так, начни заново через кнопку.")
        return

    ok = await db.set_media_trigger(message.from_user.id, media_id, "keywords", keywords)
    await state.clear()

    if not ok:
        await message.answer("Доступ утерян, изменения не сохранены.")
        return

    await _prompt_reply_text_step(message, media_id)


async def _prompt_reply_text_step(message: Message, media_id: str) -> None:
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="📝 Текст ответа",
            callback_data=ConfigureReplyTextCallback(media_id=media_id).pack(),
        )
    ]])
    await message.answer("Условия сохранены. Дальше — текст ответа в Директ.", reply_markup=keyboard)


@router.callback_query(ConfigureReplyTextCallback.filter())
async def configure_reply_text_handler(
    callback: CallbackQuery, callback_data: ConfigureReplyTextCallback, state: FSMContext
) -> None:
    if callback.from_user is None:
        return

    await state.update_data(media_id=callback_data.media_id)
    await state.set_state(MediaWizardStates.waiting_for_reply_text)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Пропустить (использовать текст по умолчанию)",
            callback_data=SkipReplyTextCallback(media_id=callback_data.media_id).pack(),
        )
    ]])
    await callback.message.answer(
        "📝 <b>Шаг 3/3.</b> Пришли текст, который бот отправит в Директ под этим постом. "
        "Тег <code>{username}</code> подставит имя клиента из Instagram.\n\n"
        "Для отмены — /cancel",
        reply_markup=keyboard,
    )
    await _safe_answer(callback)


@router.callback_query(SkipReplyTextCallback.filter())
async def skip_reply_text_handler(callback: CallbackQuery, callback_data: SkipReplyTextCallback, state: FSMContext, db: Database) -> None:
    if callback.from_user is None:
        return

    await state.clear()
    await _show_media_card(callback.message, db, callback_data.media_id)
    await _safe_answer(callback)


@router.message(MediaWizardStates.waiting_for_reply_text)
async def receive_reply_text_handler(message: Message, state: FSMContext, db: Database) -> None:
    if message.from_user is None:
        return

    new_text = (message.text or "").strip()
    if not new_text:
        await message.answer("Текст не может быть пустым. Пришли текст ещё раз или /cancel для отмены.")
        return

    data = await state.get_data()
    media_id = data.get("media_id")
    if not media_id:
        await state.clear()
        await message.answer("Что-то пошло не так, начни заново через кнопку.")
        return

    ok = await db.set_media_reply_text(message.from_user.id, media_id, new_text)
    await state.clear()

    if not ok:
        await message.answer("Доступ утерян, изменения не сохранены.")
        return

    await _show_media_card(message, db, media_id)


async def _show_media_card(message: Message, db: Database, media_id: str) -> None:
    media_row = await db.get_monitored_media(media_id)
    if media_row is None:
        await message.answer("Пост не найден — возможно, был удалён.")
        return
    text, keyboard = _build_media_card(media_row)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(ToggleMediaActiveCallback.filter())
async def toggle_media_active_handler(
    callback: CallbackQuery, callback_data: ToggleMediaActiveCallback, db: Database
) -> None:
    if callback.from_user is None:
        return

    current = await db.get_monitored_media(callback_data.media_id)
    if current is None:
        await _safe_answer(callback, "Пост не найден", show_alert=True)
        return

    updated = await db.set_media_active(callback.from_user.id, callback_data.media_id, not current["is_active"])
    if updated is None:
        await _safe_answer(callback, "Доступ утерян", show_alert=True)
        return

    text, keyboard = _build_media_card(updated)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await _safe_answer(callback, "Запущено 🚀" if updated["is_active"] else "Остановлено ⏸")


@router.callback_query(DeleteMediaCallback.filter())
async def delete_media_handler(callback: CallbackQuery, callback_data: DeleteMediaCallback, db: Database) -> None:
    if callback.from_user is None:
        return

    deleted = await db.delete_monitored_media(callback.from_user.id, callback_data.media_id)
    if not deleted:
        await _safe_answer(callback, "Доступ утерян или пост уже удалён", show_alert=True)
        return

    await callback.message.edit_text("🗑 Пост удалён из панели, бот больше не следит за его комментариями.")
    await _safe_answer(callback, "Удалено")
