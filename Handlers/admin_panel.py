from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from DB.database import Database
from config import settings

logger = logging.getLogger(__name__)

router = Router(name="admin_panel")

STATUS_LABELS = {
    "new": "🆕 Новый",
    "notified": "📨 Уведомлён",
    "phone_requested": "📞 Запрошен телефон",
    "phone_received": "✅ Телефон получен",
    "closed": "🔒 Закрыт",
}


class ClientToggleCallback(CallbackData, prefix="client_toggle"):
    ig_business_id: str


def _is_owner(message: Message) -> bool:
    return message.from_user is not None and message.from_user.id == settings.MANAGER_CHAT_ID


def _compute_status_line(client, now: datetime) -> str:
    trial_ends_at = client["trial_starts_at"] + timedelta(days=settings.TRIAL_DAYS)
    subscription_expires_at = client["subscription_expires_at"]

    if subscription_expires_at is not None and subscription_expires_at > now:
        days_left = (subscription_expires_at - now).days
        return (
            f"✅ Подписка (${settings.SUBSCRIPTION_PRICE_USD}/мес) до "
            f"{subscription_expires_at:%d.%m.%Y %H:%M} UTC, осталось {days_left} дн."
        )
    if now < trial_ends_at:
        hours_left = int((trial_ends_at - now).total_seconds() // 3600)
        return f"🕒 Триал активен, осталось {hours_left} ч. (до {trial_ends_at:%d.%m.%Y %H:%M} UTC)"
    return "⛔ Триал и подписка истекли — перехват лидов отключён"


@router.message(Command("stats"))
async def stats_handler(message: Message, db: Database) -> None:
    if not _is_owner(message):
        return

    stats = await db.get_leads_stats()
    text = (
        "📊 <b>Статистика лидов LeadArmor</b>\n\n"
        f"Всего лидов: <b>{stats['total']}</b>\n"
        f"🌿 Органика: {stats['organic']}\n"
        f"🎯 Таргет: {stats['ad']}"
    )
    await message.answer(text)


@router.message(Command("billing"))
async def billing_handler(message: Message, command: CommandObject, db: Database) -> None:
    if not _is_owner(message):
        return

    ig_business_id = (command.args or "").strip()
    if not ig_business_id:
        await message.answer("Использование: <code>/billing ig_business_id</code>")
        return

    client = await db.get_client_billing(ig_business_id)
    if client is None:
        await message.answer(f"Клиент с ig_business_id=<code>{ig_business_id}</code> не найден")
        return

    now = datetime.now(timezone.utc)
    text = (
        f"💳 <b>Биллинг: {html.escape(client['name'])}</b>\n\n"
        f"ig_business_id: <code>{client['ig_business_id']}</code>\n"
        f"Аккаунт активен: {'да' if client['is_active'] else 'нет'}\n"
        f"{_compute_status_line(client, now)}"
    )
    await message.answer(text)


async def _build_clients_view(db: Database) -> tuple[str, InlineKeyboardMarkup | None]:
    clients = await db.list_clients()
    if not clients:
        return "Клиентов пока нет. Добавь через /add_client", None

    now = datetime.now(timezone.utc)
    lines = ["👥 <b>Клиенты LeadArmor</b>\n"]
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for client in clients:
        active_mark = "🟢" if client["is_active"] else "🔴"
        lines.append(
            f"{active_mark} <b>{html.escape(client['name'])}</b> "
            f"(<code>{client['ig_business_id']}</code>) — {_compute_status_line(client, now)}"
        )
        toggle_text = "🔴 Деактивировать" if client["is_active"] else "🟢 Активировать"
        keyboard_rows.append([
            InlineKeyboardButton(
                text=f"{toggle_text}: {client['name']}",
                callback_data=ClientToggleCallback(ig_business_id=client["ig_business_id"]).pack(),
            )
        ])

    if settings.GOOGLE_SHEETS_SPREADSHEET_ID:
        keyboard_rows.append([
            InlineKeyboardButton(
                text="📊 Открыть Google Sheets",
                url=f"https://docs.google.com/spreadsheets/d/{settings.GOOGLE_SHEETS_SPREADSHEET_ID}/edit",
            )
        ])

    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


@router.message(Command("clients"))
async def clients_handler(message: Message, db: Database) -> None:
    if not _is_owner(message):
        return

    text, keyboard = await _build_clients_view(db)
    await message.answer(text, reply_markup=keyboard)


@router.callback_query(ClientToggleCallback.filter())
async def toggle_client_handler(
    callback: CallbackQuery,
    callback_data: ClientToggleCallback,
    db: Database,
) -> None:
    if callback.from_user is None or callback.from_user.id != settings.MANAGER_CHAT_ID:
        await callback.answer("Только для владельца", show_alert=True)
        return

    updated = await db.toggle_client_active(callback_data.ig_business_id)
    if updated is None:
        await callback.answer("Клиент не найден", show_alert=True)
        return

    status_text = "включён 🟢" if updated["is_active"] else "выключен 🔴"
    await callback.answer(f"{updated['name']} {status_text}")

    text, keyboard = await _build_clients_view(db)
    await callback.message.edit_text(text, reply_markup=keyboard)


@router.message(Command("recent"))
async def recent_leads_handler(message: Message, db: Database) -> None:
    if not _is_owner(message):
        return

    leads = await db.get_recent_leads(limit=10)
    if not leads:
        await message.answer("Лидов пока нет")
        return

    lines = ["🕒 <b>Последние 10 лидов</b>\n"]
    for lead in leads:
        source = "🎯" if lead["post_type"] == "ad" else "🌿"
        phone = lead["phone_number"] or "—"
        status_label = STATUS_LABELS.get(lead["status"], lead["status"])
        username = html.escape(lead["ig_username"]) if lead["ig_username"] else "?"
        lines.append(
            f"{source} @{username} ({html.escape(lead['client_name'])}) — "
            f"{status_label}, тел: {phone} — {lead['created_at']:%d.%m %H:%M}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("add_client"))
async def add_client_handler(message: Message, command: CommandObject, db: Database) -> None:
    if not _is_owner(message):
        return

    raw_args = command.args or ""
    parts = [part.strip() for part in raw_args.split("|")]
    if len(parts) != 4 or not all(parts):
        await message.answer(
            "Использование:\n"
            "<code>/add_client Название|ig_business_id|page_access_token|manager_chat_id</code>\n\n"
            "Пример:\n"
            "<code>/add_client AutoSalon Tashkent|17841400000000000|EAAG...|987654321</code>"
        )
        return

    name, ig_business_id, page_access_token, manager_chat_id_raw = parts
    if not manager_chat_id_raw.isdigit():
        await message.answer("manager_chat_id должен быть числом (Telegram chat id менеджера)")
        return

    client_id = await db.upsert_client(
        name=name,
        ig_business_id=ig_business_id,
        page_access_token=page_access_token,
        manager_chat_id=int(manager_chat_id_raw),
    )
    masked_token = (
        f"{page_access_token[:10]}…{page_access_token[-4:]}"
        if len(page_access_token) > 14
        else "***"
    )
    await message.answer(
        f"✅ Клиент добавлен (id={client_id})\n"
        f"Название: {html.escape(name)}\n"
        f"ig_business_id: <code>{ig_business_id}</code>\n"
        f"Токен: <code>{masked_token}</code>\n"
        f"Уведомления менеджеру: чат <code>{manager_chat_id_raw}</code>\n\n"
        f"Триал на {settings.TRIAL_DAYS} дня стартовал автоматически."
    )


@router.message(Command("extend_subscription"))
async def extend_subscription_handler(message: Message, command: CommandObject, db: Database) -> None:
    if not _is_owner(message):
        return

    args = (command.args or "").split()
    if len(args) != 2 or not args[1].isdigit():
        await message.answer("Использование: <code>/extend_subscription ig_business_id дни</code>")
        return

    ig_business_id, days_raw = args
    days = int(days_raw)
    if days <= 0:
        await message.answer("Количество дней должно быть положительным")
        return

    updated = await db.extend_subscription(ig_business_id, days)
    if updated is None:
        await message.answer(f"Клиент с ig_business_id=<code>{ig_business_id}</code> не найден")
        return

    await message.answer(
        f"✅ Подписка «{html.escape(updated['name'])}» продлена на {days} дн., "
        f"действует до {updated['subscription_expires_at']:%d.%m.%Y %H:%M} UTC"
    )
