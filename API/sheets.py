from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime

import gspread
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials

from config import settings

logger = logging.getLogger(__name__)

SERVICE_ACCOUNT_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
OAUTH_SCOPES = ["https://www.googleapis.com/auth/drive.file"]
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"

HEADER_ROW = ["Дата", "Instagram", "Телефон", "Источник", "Комментарий скрыт", "Статус лида"]

SOURCE_LABELS = {"ad": "Таргет (реклама)", "organic": "Органика"}
STATUS_LABELS = {
    "new": "Новый",
    "notified": "Менеджер уведомлён",
    "phone_requested": "Запрошен телефон",
    "phone_received": "Телефон получен",
    "closed": "Закрыт",
}

_service_client: gspread.Client | None = None
_service_client_init_failed = False

_oauth_client: gspread.Client | None = None
_oauth_client_init_failed = False


def _get_service_client() -> gspread.Client | None:
    global _service_client, _service_client_init_failed
    if _service_client is not None:
        return _service_client
    if _service_client_init_failed:
        return None

    try:
        creds = ServiceAccountCredentials.from_service_account_file(
            settings.GOOGLE_SHEETS_CREDENTIALS_FILE, scopes=SERVICE_ACCOUNT_SCOPES
        )
        _service_client = gspread.authorize(creds)
        return _service_client
    except FileNotFoundError:
        logger.warning(
            "Файл %s не найден — Google Sheets синхронизация отключена",
            settings.GOOGLE_SHEETS_CREDENTIALS_FILE,
        )
    except Exception:
        logger.exception("Не удалось авторизоваться service account'ом в Google Sheets")

    _service_client_init_failed = True
    return None


def _get_service_account_email() -> str | None:
    try:
        with open(settings.GOOGLE_SHEETS_CREDENTIALS_FILE) as f:
            return json.load(f).get("client_email")
    except Exception:
        return None


def _get_oauth_client() -> gspread.Client | None:
    global _oauth_client, _oauth_client_init_failed
    if _oauth_client is not None:
        return _oauth_client
    if _oauth_client_init_failed:
        return None

    if not (
        settings.GOOGLE_OAUTH_CLIENT_ID
        and settings.GOOGLE_OAUTH_CLIENT_SECRET
        and settings.GOOGLE_OAUTH_REFRESH_TOKEN
    ):
        _oauth_client_init_failed = True
        return None

    try:
        creds = OAuthCredentials(
            token=None,
            refresh_token=settings.GOOGLE_OAUTH_REFRESH_TOKEN,
            client_id=settings.GOOGLE_OAUTH_CLIENT_ID,
            client_secret=settings.GOOGLE_OAUTH_CLIENT_SECRET,
            token_uri=GOOGLE_TOKEN_URI,
            scopes=OAUTH_SCOPES,
        )
        _oauth_client = gspread.authorize(creds)
        return _oauth_client
    except Exception:
        logger.exception("Не удалось авторизоваться через OAuth владельца в Google")

    _oauth_client_init_failed = True
    return None


def _create_client_spreadsheet(client_name: str) -> str | None:
    oauth_client = _get_oauth_client()
    if oauth_client is None:
        logger.info(
            "OAuth владельца не настроен (GOOGLE_OAUTH_REFRESH_TOKEN) — "
            "отдельная таблица для %r не создана, будет использована общая",
            client_name,
        )
        return None

    try:
        spreadsheet = oauth_client.create(f"LeadArmor — {client_name}")
        worksheet = spreadsheet.sheet1
        worksheet.update_title("Leads")
        worksheet.append_row(HEADER_ROW)

        service_account_email = _get_service_account_email()
        if service_account_email:
            spreadsheet.share(service_account_email, perm_type="user", role="writer", notify=False)

        spreadsheet.share(None, perm_type="anyone", role="reader", with_link=True)

        logger.info("Создана отдельная Google-таблица для %r: %s", client_name, spreadsheet.id)
        return spreadsheet.id
    except Exception:
        logger.exception("Не удалось создать отдельную Google-таблицу для %r", client_name)
        return None


async def create_client_spreadsheet(client_name: str) -> str | None:
    return await asyncio.to_thread(_create_client_spreadsheet, client_name)


def _append_confirmed_lead(
    client_name: str,
    ig_username: str | None,
    phone_number: str,
    source: str,
    is_hidden: bool,
    status: str,
    sheet_id: str | None,
) -> None:
    client = _get_service_client()
    if client is None:
        return

    row = [
        datetime.now().strftime("%d.%m.%Y %H:%M"),
        f"@{ig_username}" if ig_username else "",
        phone_number,
        SOURCE_LABELS.get(source, source),
        "Да" if is_hidden else "Нет",
        STATUS_LABELS.get(status, status),
    ]

    try:
        if sheet_id:
            spreadsheet = client.open_by_key(sheet_id)
            worksheet = spreadsheet.sheet1
        else:
            if not settings.GOOGLE_SHEETS_SPREADSHEET_ID:
                logger.warning("Нет ни личной, ни общей таблицы — пропускаем запись в Sheets")
                return
            spreadsheet = client.open_by_key(settings.GOOGLE_SHEETS_SPREADSHEET_ID)
            try:
                worksheet = spreadsheet.worksheet(client_name)
            except gspread.WorksheetNotFound:
                worksheet = spreadsheet.add_worksheet(title=client_name, rows=1000, cols=len(HEADER_ROW))
                worksheet.append_row(HEADER_ROW)

        worksheet.append_row(row)
        logger.info("Лид @%s записан в Google Sheets (%s)", ig_username, sheet_id or client_name)
    except Exception:
        logger.exception("Не удалось записать лид в Google Sheets (клиент %r)", client_name)


async def append_confirmed_lead(
    client_name: str,
    ig_username: str | None,
    phone_number: str,
    source: str,
    is_hidden: bool,
    status: str,
    sheet_id: str | None = None,
) -> None:
    await asyncio.to_thread(
        _append_confirmed_lead, client_name, ig_username, phone_number, source, is_hidden, status, sheet_id
    )
