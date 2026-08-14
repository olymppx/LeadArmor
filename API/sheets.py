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


def _load_service_account_info() -> dict | None:
    """Читает ключ service account: сперва из переменной окружения, потом с диска.

    На хостинге (Render/Koyeb) файла credentials.json нет и быть не должно —
    он в .gitignore и не попадает в репозиторий. Поэтому там содержимое
    ключа кладётся целиком в переменную GOOGLE_SHEETS_CREDENTIALS_JSON.
    Локально по-прежнему работает обычный файл.
    """
    raw_json = settings.GOOGLE_SHEETS_CREDENTIALS_JSON.strip()
    if raw_json:
        try:
            return json.loads(raw_json)
        except json.JSONDecodeError:
            logger.exception("GOOGLE_SHEETS_CREDENTIALS_JSON задан, но это не валидный JSON")
            return None

    try:
        with open(settings.GOOGLE_SHEETS_CREDENTIALS_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(
            "Нет ни GOOGLE_SHEETS_CREDENTIALS_JSON, ни файла %s — Google Sheets отключён",
            settings.GOOGLE_SHEETS_CREDENTIALS_FILE,
        )
    except Exception:
        logger.exception("Не удалось прочитать ключ service account")
    return None


def _get_service_client() -> gspread.Client | None:
    global _service_client, _service_client_init_failed
    if _service_client is not None:
        return _service_client
    if _service_client_init_failed:
        return None

    info = _load_service_account_info()
    if info is not None:
        try:
            creds = ServiceAccountCredentials.from_service_account_info(
                info, scopes=SERVICE_ACCOUNT_SCOPES
            )
            _service_client = gspread.authorize(creds)
            return _service_client
        except Exception:
            logger.exception("Не удалось авторизоваться service account'ом в Google Sheets")

    _service_client_init_failed = True
    return None


def _get_service_account_email() -> str | None:
    info = _load_service_account_info()
    return info.get("client_email") if info else None


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


def _open_worksheet(client: gspread.Client, sheet_id: str | None, client_name: str) -> gspread.Worksheet | None:
    if sheet_id:
        spreadsheet = client.open_by_key(sheet_id)
        return spreadsheet.sheet1

    if not settings.GOOGLE_SHEETS_SPREADSHEET_ID:
        logger.warning("Нет ни личной, ни общей таблицы — пропускаем запись в Sheets")
        return None

    spreadsheet = client.open_by_key(settings.GOOGLE_SHEETS_SPREADSHEET_ID)
    try:
        return spreadsheet.worksheet(client_name)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=client_name, rows=1000, cols=len(HEADER_ROW))
        worksheet.append_row(HEADER_ROW)
        return worksheet


def _append_confirmed_lead(
    sheet_id: str | None,
    client_name: str,
    ig_username: str | None,
    phone_number: str,
    post_type: str,
    is_hidden: bool,
    status: str,
) -> None:
    client = _get_service_client()
    if client is None:
        return

    row = [
        datetime.now().strftime("%d.%m.%Y %H:%M"),
        f"@{ig_username}" if ig_username else "",
        phone_number,
        SOURCE_LABELS.get(post_type, post_type),
        "Да" if is_hidden else "Нет",
        STATUS_LABELS.get(status, status),
    ]

    try:
        worksheet = _open_worksheet(client, sheet_id, client_name)
        if worksheet is None:
            return

        worksheet.append_row(row, value_input_option="RAW")
        logger.info("Готовый лид @%s записан в Google Sheets (%s)", ig_username, sheet_id or client_name)
    except Exception:
        logger.exception("Не удалось записать готовый лид в Google Sheets (клиент %r)", client_name)


async def append_confirmed_lead(
    sheet_id: str | None,
    client_name: str,
    ig_username: str | None,
    phone_number: str,
    post_type: str,
    is_hidden: bool,
    status: str,
) -> None:
    await asyncio.to_thread(
        _append_confirmed_lead, sheet_id, client_name, ig_username, phone_number, post_type, is_hidden, status
    )
