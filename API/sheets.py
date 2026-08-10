from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from config import settings

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
HEADER_ROW = ["Дата", "Instagram", "Телефон", "Источник", "Комментарий скрыт", "Статус лида"]

SOURCE_LABELS = {"ad": "Таргет (реклама)", "organic": "Органика"}
STATUS_LABELS = {
    "new": "Новый",
    "notified": "Менеджер уведомлён",
    "phone_requested": "Запрошен телефон",
    "phone_received": "Телефон получен",
    "closed": "Закрыт",
}

_client: gspread.Client | None = None
_client_init_failed = False


def _get_client() -> gspread.Client | None:
    global _client, _client_init_failed
    if _client is not None:
        return _client
    if _client_init_failed:
        return None

    try:
        creds = Credentials.from_service_account_file(
            settings.GOOGLE_SHEETS_CREDENTIALS_FILE, scopes=SCOPES
        )
        _client = gspread.authorize(creds)
        return _client
    except FileNotFoundError:
        logger.warning(
            "Файл %s не найден — Google Sheets синхронизация отключена, "
            "пока не настроен service account",
            settings.GOOGLE_SHEETS_CREDENTIALS_FILE,
        )
    except Exception:
        logger.exception("Не удалось авторизоваться в Google Sheets")

    _client_init_failed = True
    return None


def _append_confirmed_lead(
    client_name: str,
    ig_username: str | None,
    phone_number: str,
    source: str,
    is_hidden: bool,
    status: str,
) -> None:
    client = _get_client()
    if client is None:
        return
    if not settings.GOOGLE_SHEETS_SPREADSHEET_ID:
        logger.warning("GOOGLE_SHEETS_SPREADSHEET_ID не задан — пропускаем запись в Sheets")
        return

    try:
        spreadsheet = client.open_by_key(settings.GOOGLE_SHEETS_SPREADSHEET_ID)
        try:
            worksheet = spreadsheet.worksheet(client_name)
        except gspread.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(title=client_name, rows=1000, cols=len(HEADER_ROW))
            worksheet.append_row(HEADER_ROW)

        worksheet.append_row([
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            f"@{ig_username}" if ig_username else "",
            phone_number,
            SOURCE_LABELS.get(source, source),
            "Да" if is_hidden else "Нет",
            STATUS_LABELS.get(status, status),
        ])
        logger.info("Лид @%s записан в Google Sheets (лист %r)", ig_username, client_name)
    except Exception:
        logger.exception("Не удалось записать лид в Google Sheets (клиент %r)", client_name)


async def append_confirmed_lead(
    client_name: str,
    ig_username: str | None,
    phone_number: str,
    source: str,
    is_hidden: bool,
    status: str,
) -> None:
    await asyncio.to_thread(
        _append_confirmed_lead, client_name, ig_username, phone_number, source, is_hidden, status
    )
