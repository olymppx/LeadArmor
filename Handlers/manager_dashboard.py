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

from API.meta_api import THANK_YOU_TEXT, build_private_reply_text, fetch_media_title, resolve_owned_media_id
from DB.database import Database
from manager_views import (
    AddMediaCallback,
    ListMediaCallback,
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


class MediaWizardStates(StatesGroup):
    waiting_for_media_link = State()
    waiting_for_keywords = State()
    waiting_for_reply_text = State()
    waiting_for_thank_you_text = State()


class ConfigureTriggerCallback(CallbackData, prefix="cfg_trigger"):
    media_id: str


class SetTriggerTypeCallback(CallbackData, prefix="set_trigger_type"):
    media_id: str
    trigger_type: str


class ConfigureReplyTextCallback(CallbackData, prefix="cfg_reply_text"):
    media_id: str


class SkipReplyTextCallback(CallbackData, prefix="skip_reply_text"):
    media_id: str


class ConfigureMediaThankYouCallback(CallbackData, prefix="cfg_media_thanks"):
    media_id: str


class ToggleMediaActiveCallback(CallbackData, prefix="toggle_media"):
    media_id: str


class ToggleHideCommentsCallback(CallbackData, prefix="toggle_hide"):
    media_id: str


class DeleteMediaCallback(CallbackData, prefix="delete_media"):
    media_id: str


class OpenMediaCallback(CallbackData, prefix="open_media"):
    media_id: str


class SetPostTypeOverrideCallback(CallbackData, prefix="set_post_type"):
    media_id: str
    value: str  # "auto" | "organic" | "ad"


TRIGGER_LABELS = {"all_comments": "Все комментарии", "keywords": "Ключевые слова"}
POST_TYPE_LABELS = {"organic": "🌿 Всегда органика", "ad": "🎯 Всегда таргет", None: "🤖 Авто (по Meta)"}


def _media_display_name(media_row, max_len: int = 40) -> str:
    # В списке/карточке показываем подпись поста, а не голый media_id —
    # список из чисел вида 180721940... ничего не говорит владельцу.
    title = media_row["title"]
    if not title:
        return media_row["media_id"]
    title = title.replace("\n", " ").strip()
    return title if len(title) <= max_len else title[:max_len - 1] + "…"


def _build_media_card(media_row) -> tuple[str, InlineKeyboardMarkup]:
    trigger_label = TRIGGER_LABELS.get(media_row["trigger_type"], media_row["trigger_type"])
    status_label = "🟢 Активен" if media_row["is_active"] else "🔴 Выключен"
    post_type_label = POST_TYPE_LABELS.get(media_row["post_type_override"], media_row["post_type_override"])

    lines = [
        "📋 <b>Карточка публикации</b>\n",
        f"<b>Название:</b> {html.escape(_media_display_name(media_row, max_len=80))}",
        f"<b>Пост:</b> <code>{html.escape(media_row['media_id'])}</code>",
        f"<b>Тип поста:</b> {post_type_label}",
        f"<b>Триггер:</b> {trigger_label}",
    ]
    if media_row["trigger_type"] == "keywords":
        keywords = media_row["keywords_list"] or []
        lines.append(f"<b>Ключевые слова:</b> {', '.join(keywords) if keywords else '—'}")
    lines.append(f"<b>Текст ответа:</b> {'настроен ✏️' if media_row['reply_text'] else 'по умолчанию'}")
    lines.append(f"<b>Thank-you текст:</b> {'настроен ✏️' if media_row['thank_you_text'] else 'по умолчанию'}")
    lines.append(f"<b>Скрытие коммента под таргетом:</b> {'🙈 включено' if media_row['hide_comments'] else '👁 выключено'}")
    lines.append(f"\n<b>Статус:</b> {status_label}")

    toggle_text = "⏸ Остановить щит" if media_row["is_active"] else "🚀 ЗАПУСТИТЬ ЩИТ"
    hide_toggle_text = "👁 Не скрывать коммент" if media_row["hide_comments"] else "🙈 Скрывать коммент"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=toggle_text,
            callback_data=ToggleMediaActiveCallback(media_id=media_row["media_id"]).pack(),
        )],
        [InlineKeyboardButton(
            text="📝 Текст ответа",
            callback_data=ConfigureReplyTextCallback(media_id=media_row["media_id"]).pack(),
        )],
        [InlineKeyboardButton(
            text="🙏 Thank-you текст",
            callback_data=ConfigureMediaThankYouCallback(media_id=media_row["media_id"]).pack(),
        )],
        [InlineKeyboardButton(
            text=hide_toggle_text,
            callback_data=ToggleHideCommentsCallback(media_id=media_row["media_id"]).pack(),
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


@router.message(
    StateFilter(
        MediaWizardStates.waiting_for_media_link,
        MediaWizardStates.waiting_for_keywords,
        MediaWizardStates.waiting_for_reply_text,
        MediaWizardStates.waiting_for_thank_you_text,
    ),
    Command("cancel"),
)
async def cancel_media_wizard_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Отменено, изменения не сохранены.")


@router.callback_query(ListMediaCallback.filter())
async def list_media_handler(callback: CallbackQuery, db: Database) -> None:
    if callback.from_user is None:
        return

    client = await db.get_client_by_manager_chat_id(callback.from_user.id)
    if client is None:
        await _safe_answer(callback, "Доступ утерян", show_alert=True)
        return

    media_list = await db.list_monitored_media(client["ig_business_id"])
    if not media_list:
        await callback.message.answer("Пока нет ни одного добавленного поста.")
        await _safe_answer(callback)
        return

    keyboard_rows = []
    for media_row in media_list:
        status_dot = "🟢" if media_row["is_active"] else "🔴"
        trigger_label = TRIGGER_LABELS.get(media_row["trigger_type"], media_row["trigger_type"])
        keyboard_rows.append([InlineKeyboardButton(
            text=f"{status_dot} {_media_display_name(media_row)} — {trigger_label}",
            callback_data=OpenMediaCallback(media_id=media_row["media_id"]).pack(),
        )])

    await callback.message.answer(
        f"📋 <b>Все подключённые посты ({len(media_list)}):</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard_rows),
    )
    await _safe_answer(callback)


@router.callback_query(OpenMediaCallback.filter())
async def open_media_handler(callback: CallbackQuery, callback_data: OpenMediaCallback, db: Database) -> None:
    if callback.from_user is None:
        return

    await _show_media_card(callback.message, db, callback_data.media_id)
    await _safe_answer(callback)


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
        "🔗 <b>Шаг 1/4.</b> Пришли ссылку на пост/видео/рекламный креатив в Instagram "
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

    title = await fetch_media_title(http_session, media_id, client["page_access_token"])
    status = await db.add_media_to_monitor(client["ig_business_id"], media_id, title=title)
    await state.clear()

    if status == "conflict":
        await message.answer("⚠️ Этот пост уже добавлен на другом аккаунте.")
        return

    if status == "exists":
        # Пост уже был добавлен раньше — показываем ЕГО ТЕКУЩУЮ карточку,
        # а не гоним по мастеру заново (это стирало сохранённые условия/текст).
        await message.answer(
            f"ℹ️ Этот пост уже добавлен (media_id=<code>{html.escape(media_id)}</code>). "
            "Вот его текущая настройка:"
        )
        await _show_media_card(message, db, media_id)
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🤖 Авто (по Meta)",
            callback_data=SetPostTypeOverrideCallback(media_id=media_id, value="auto").pack(),
        )],
        [InlineKeyboardButton(
            text="🌿 Всегда органика",
            callback_data=SetPostTypeOverrideCallback(media_id=media_id, value="organic").pack(),
        )],
        [InlineKeyboardButton(
            text="🎯 Всегда таргет",
            callback_data=SetPostTypeOverrideCallback(media_id=media_id, value="ad").pack(),
        )],
    ])
    added_label = html.escape(title) if title else f"media_id=<code>{html.escape(media_id)}</code>"
    await message.answer(
        f"✅ Пост добавлен: {added_label}\n\n"
        "🎯 <b>Шаг 2/4.</b> Какого типа ваш пост?\n\n"
        "🌿 <b>Органика</b> — обычный пост, без рекламного бюджета\n"
        "🎯 <b>Таргет</b> — платная реклама (у неё скрываются комменты от конкурентов)\n\n"
        "Не уверены — жмите 🤖 Авто, бот сам разберётся по каждому комментарию.",
        reply_markup=keyboard,
    )


@router.callback_query(SetPostTypeOverrideCallback.filter())
async def set_post_type_override_handler(
    callback: CallbackQuery, callback_data: SetPostTypeOverrideCallback, db: Database
) -> None:
    if callback.from_user is None:
        return

    override_value = None if callback_data.value == "auto" else callback_data.value
    ok = await db.set_media_post_type_override(callback.from_user.id, callback_data.media_id, override_value)
    if not ok:
        await _safe_answer(callback, "Доступ утерян", show_alert=True)
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="⚙️ Настроить условия",
            callback_data=ConfigureTriggerCallback(media_id=callback_data.media_id).pack(),
        )
    ]])
    await callback.message.answer("Тип поста сохранён. Дальше — на какие комментарии реагировать.", reply_markup=keyboard)
    await _safe_answer(callback)


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
    await callback.message.answer("⚙️ <b>Шаг 3/4.</b> На какие комментарии реагировать?", reply_markup=keyboard)
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
    callback: CallbackQuery, callback_data: ConfigureReplyTextCallback, state: FSMContext, db: Database
) -> None:
    if callback.from_user is None:
        return

    media_row = await db.get_monitored_media(callback_data.media_id)
    if media_row is None:
        await _safe_answer(callback, "Пост не найден", show_alert=True)
        return

    current_text = media_row["reply_text"] or build_private_reply_text(None)
    # Пост уже активен = это правка с карточки, а не первичная настройка —
    # ярлык "Шаг 4/4" был бы враньём, показываем нейтральный заголовок.
    header = "📝 <b>Шаг 4/4.</b>" if not media_row["is_active"] else "✏️ <b>Изменить текст ответа.</b>"

    await state.update_data(media_id=callback_data.media_id)
    await state.set_state(MediaWizardStates.waiting_for_reply_text)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="Пропустить (использовать текст по умолчанию)",
            callback_data=SkipReplyTextCallback(media_id=callback_data.media_id).pack(),
        )
    ]])
    await callback.message.answer(
        f"{header} Текущий текст:\n\n"
        f"<code>{html.escape(current_text)}</code>\n\n"
        "Пришли новый текст, который бот отправит в Директ под этим постом. "
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


@router.callback_query(ConfigureMediaThankYouCallback.filter())
async def configure_media_thank_you_handler(
    callback: CallbackQuery, callback_data: ConfigureMediaThankYouCallback, state: FSMContext, db: Database
) -> None:
    if callback.from_user is None:
        return

    media_row = await db.get_monitored_media(callback_data.media_id)
    if media_row is None:
        await _safe_answer(callback, "Пост не найден", show_alert=True)
        return

    current_text = media_row["thank_you_text"] or THANK_YOU_TEXT

    await state.update_data(media_id=callback_data.media_id)
    await state.set_state(MediaWizardStates.waiting_for_thank_you_text)

    await callback.message.answer(
        "✏️ <b>Текущий Thank-you текст этого поста (после получения номера):</b>\n\n"
        f"<code>{html.escape(current_text)}</code>\n\n"
        "Пришли новый текст. Тег <code>{username}</code> необязателен.\n\n"
        "Для отмены — /cancel"
    )
    await _safe_answer(callback)


@router.message(MediaWizardStates.waiting_for_thank_you_text)
async def receive_media_thank_you_handler(message: Message, state: FSMContext, db: Database) -> None:
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

    ok = await db.set_media_thank_you_text(message.from_user.id, media_id, new_text)
    await state.clear()

    if not ok:
        await message.answer("Доступ утерян, изменения не сохранены.")
        return

    await _show_media_card(message, db, media_id)


async def _send_home_nudge(message: Message, db: Database) -> None:
    # После карточки поста легко потерять остальные фичи (статистика, Direct/
    # Thank-you тексты, ссылка на Sheets) — они были доступны только через
    # /mystats, о которой надо вспомнить. Подсовываем их обратно сразу же.
    home_view = await build_manager_home_view(db, message.chat.id)
    if home_view is not None:
        home_text, home_keyboard = home_view
        await message.answer(home_text, reply_markup=home_keyboard)


async def _show_media_card(message: Message, db: Database, media_id: str) -> None:
    media_row = await db.get_monitored_media(media_id)
    if media_row is None:
        await message.answer("Пост не найден — возможно, был удалён.")
        return
    text, keyboard = _build_media_card(media_row)
    await message.answer(text, reply_markup=keyboard)
    await _send_home_nudge(message, db)


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
    if updated["is_active"]:
        await _send_home_nudge(callback.message, db)


@router.callback_query(ToggleHideCommentsCallback.filter())
async def toggle_hide_comments_handler(
    callback: CallbackQuery, callback_data: ToggleHideCommentsCallback, db: Database
) -> None:
    if callback.from_user is None:
        return

    current = await db.get_monitored_media(callback_data.media_id)
    if current is None:
        await _safe_answer(callback, "Пост не найден", show_alert=True)
        return

    updated = await db.set_media_hide_comments(
        callback.from_user.id, callback_data.media_id, not current["hide_comments"]
    )
    if updated is None:
        await _safe_answer(callback, "Доступ утерян", show_alert=True)
        return

    text, keyboard = _build_media_card(updated)
    await callback.message.edit_text(text, reply_markup=keyboard)
    await _safe_answer(callback, "Скрытие включено 🙈" if updated["hide_comments"] else "Скрытие выключено 👁")


@router.callback_query(DeleteMediaCallback.filter())
async def delete_media_handler(callback: CallbackQuery, callback_data: DeleteMediaCallback, db: Database) -> None:
    if callback.from_user is None:
        return

    deleted = await db.delete_monitored_media(callback.from_user.id, callback_data.media_id)
    if not deleted:
        await _safe_answer(callback, "Доступ утерян или пост уже удалён", show_alert=True)
        return

    logger.info("Пост media_id=%s удалён из панели пользователем chat_id=%s", callback_data.media_id, callback.from_user.id)
    await callback.message.edit_text("🗑 Пост удалён из панели, бот больше не следит за его комментариями.")
    await _safe_answer(callback, "Удалено")
    await _send_home_nudge(callback.message, db)
