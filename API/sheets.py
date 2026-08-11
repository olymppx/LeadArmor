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


def _parse_row_number(updated_range: str) -> int | None:
    if "!" not in updated_range:
        return None
    cell_ref = updated_range.split("!")[1].split(":")[0]
    digits = "".join(ch for ch in cell_ref if ch.isdigit())
    return int(digits) if digits else None


def _append_new_lead_row(
    sheet_id: str | None,
    client_name: str,
    ig_username: str | None,
    post_type: str,
    is_hidden: bool,
    status: str,
) -> int | None:
    client = _get_service_client()
    if client is None:
        return None

    row = [
        datetime.now().strftime("%d.%m.%Y %H:%M"),
        f"@{ig_username}" if ig_username else "",
        "",
        SOURCE_LABELS.get(post_type, post_type),
        "Да" if is_hidden else "Нет",
        STATUS_LABELS.get(status, status),
    ]

    try:
        worksheet = _open_worksheet(client, sheet_id, client_name)
        if worksheet is None:
            return None

        response = worksheet.append_row(row, value_input_option="RAW")
        row_number = _parse_row_number(response.get("updates", {}).get("updatedRange", ""))
        logger.info(
            "Строка лида @%s создана в Google Sheets (%s), строка %s",
            ig_username, sheet_id or client_name, row_number,
        )
        return row_number
    except Exception:
        logger.exception("Не удалось создать строку лида в Google Sheets (клиент %r)", client_name)
        return None


async def append_new_lead_row(
    sheet_id: str | None,
    client_name: str,
    ig_username: str | None,
    post_type: str,
    is_hidden: bool,
    status: str,
) -> int | None:
    return await asyncio.to_thread(
        _append_new_lead_row, sheet_id, client_name, ig_username, post_type, is_hidden, status
    )


def _update_lead_row(
    sheet_id: str | None,
    client_name: str,
    row_number: int,
    phone_number: str,
    source: str,
    is_hidden: bool,
    status: str,
) -> None:
    client = _get_service_client()
    if client is None:
        return

    try:
        worksheet = _open_worksheet(client, sheet_id, client_name)
        if worksheet is None:
            return

        worksheet.update(
            values=[[
                phone_number,
                SOURCE_LABELS.get(source, source),
                "Да" if is_hidden else "Нет",
                STATUS_LABELS.get(status, status),
            ]],
            range_name=f"C{row_number}:F{row_number}",
        )
        logger.info(
            "Строка лида обновлена в Google Sheets (%s), строка %s", sheet_id or client_name, row_number
        )
    except Exception:
        logger.exception(
            "Не удалось обновить строку лида в Google Sheets (клиент %r, строка %s)", client_name, row_number
        )


async def update_lead_row(
    sheet_id: str | None,
    client_name: str,
    row_number: int | None,
    phone_number: str,
    source: str,
    is_hidden: bool,
    status: str,
) -> None:
    if not row_number:
        return
    await asyncio.to_thread(
        _update_lead_row, sheet_id, client_name, row_number, phone_number, source, is_hidden, status
    )
