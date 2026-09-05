import json
import re
import socket
import time
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ..config import APP_ROOT_DIR, TOKEN_DIR
from .. import db
from ..fields import column_to_index, index_to_column, normalize_column
from .errors import SheetWriteError


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SHEET_RETRY_ATTEMPTS = 3
SHEET_RETRY_DELAY_SECONDS = 2


def extract_spreadsheet_id(url_or_id: str) -> str:
    text = (url_or_id or "").strip()
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", text)
    return match.group(1) if match else text


class SheetsClient:
    def __init__(self, credentials_path=None, token_path=None):
        configured_credentials = credentials_path or db.setting_get("google_credentials_path")
        configured_token = token_path or db.setting_get("google_token_path")
        self.credentials_path = Path(configured_credentials or TOKEN_DIR / "google_credentials.json")
        self.token_path = Path(configured_token or TOKEN_DIR / "google_token.json")
        self.service = None

    def authenticate(self):
        creds = load_saved_credentials()
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            save_credentials(creds)
        if not creds or not creds.valid:
            creds = run_google_oauth()
        self.service = build("sheets", "v4", credentials=creds)
        return True

    def _service(self):
        if not self.service:
            self.authenticate()
        return self.service

    def _execute(self, build_request, action_message):
        last_exc = None
        for attempt in range(1, SHEET_RETRY_ATTEMPTS + 1):
            try:
                return build_request().execute(num_retries=2)
            except Exception as exc:
                last_exc = exc
                if attempt >= SHEET_RETRY_ATTEMPTS or not is_retryable_sheet_error(exc):
                    break
                time.sleep(SHEET_RETRY_DELAY_SECONDS * attempt)
        user_message = readable_sheet_error(action_message, last_exc)
        raise SheetWriteError(user_message, repr(last_exc)) from last_exc

    def read_column(self, spreadsheet_id, worksheet_name, column, start_row):
        col = normalize_column(column)
        if not col:
            return []
        rng = f"'{worksheet_name}'!{col}{start_row}:{col}"
        resp = self._execute(
            lambda: self._service().spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=rng),
            "读取表格失败",
        )
        values = resp.get("values", [])
        rows = []
        for offset, row in enumerate(values):
            rows.append({"row_number": start_row + offset, "url": row[0].strip() if row else ""})
        return rows

    def read_column_values(self, spreadsheet_id, worksheet_name, column, start_row):
        return {
            row["row_number"]: row["url"]
            for row in self.read_column(spreadsheet_id, worksheet_name, column, start_row)
        }

    def next_empty_row(self, spreadsheet_id, worksheet_name, column, start_row):
        rows = self.read_column(spreadsheet_id, worksheet_name, column, start_row)
        if not rows:
            return int(start_row or 2)
        return max(row["row_number"] for row in rows) + 1

    def read_range_rows(self, spreadsheet_id, worksheet_name, start_column, end_column, start_row):
        start = start_column.strip().upper()
        end = end_column.strip().upper()
        rng = f"'{worksheet_name}'!{start}{start_row}:{end}"
        resp = self._execute(
            lambda: self._service().spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=rng),
            "读取表格失败",
        )
        values = resp.get("values", [])
        rows = []
        for offset, row in enumerate(values):
            rows.append({"row_number": start_row + offset, "values": row})
        return rows

    def read_cell(self, spreadsheet_id, worksheet_name, column, row_number):
        col = column.strip().upper()
        rng = f"'{worksheet_name}'!{col}{row_number}:{col}{row_number}"
        resp = self._execute(
            lambda: self._service().spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=rng),
            "读取表格失败",
        )
        values = resp.get("values", [])
        return values[0][0] if values and values[0] else ""

    def write_headers(self, spreadsheet_id, worksheet_name, start_column, header_row, labels):
        start = column_to_index(start_column)
        end_col = index_to_column(start + len(labels) - 1)
        rng = f"'{worksheet_name}'!{start_column.upper()}{header_row}:{end_col}{header_row}"
        self.write_values(spreadsheet_id, rng, [labels])

    def write_row(self, spreadsheet_id, worksheet_name, start_column, row_number, values):
        start = column_to_index(start_column)
        end_col = index_to_column(start + len(values) - 1)
        rng = f"'{worksheet_name}'!{start_column.upper()}{row_number}:{end_col}{row_number}"
        self.write_values(spreadsheet_id, rng, [values])

    def write_mapped_values(self, spreadsheet_id, worksheet_name, row_number, pairs):
        data = []
        for column, value in pairs or []:
            col = normalize_column(column)
            if not col:
                continue
            data.append(
                {
                    "range": f"'{worksheet_name}'!{col}{row_number}",
                    "values": [[value]],
                }
            )
        if not data:
            return
        self._execute(
            lambda: self._service().spreadsheets().values().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"valueInputOption": "USER_ENTERED", "data": data},
            ),
            "写入表格失败",
        )

    def write_cell(self, spreadsheet_id, worksheet_name, column, row_number, value):
        col = column.strip().upper()
        rng = f"'{worksheet_name}'!{col}{row_number}:{col}{row_number}"
        self.write_values(spreadsheet_id, rng, [[value]])

    def append_row(self, spreadsheet_id, worksheet_name, start_column, values):
        col = start_column.strip().upper()
        rng = f"'{worksheet_name}'!{col}:{col}"
        self._execute(
            lambda: self._service().spreadsheets().values().append(
                spreadsheetId=spreadsheet_id,
                range=rng,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [values]},
            ),
            "写入表格失败",
        )

    def write_values(self, spreadsheet_id, range_name, values):
        self._execute(
            lambda: self._service().spreadsheets().values().update(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                body={"values": values},
            ),
            "写入表格失败",
        )


def bundled_credentials_path():
    candidates = [
        TOKEN_DIR / "google_credentials.json",
        APP_ROOT_DIR / "google_credentials.json",
        Path.cwd() / "google_credentials.json",
        Path(__file__).resolve().parents[2] / "google_credentials.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def load_saved_credentials():
    token_json = db.setting_get("google_token_json")
    if token_json:
        try:
            return Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
        except Exception:
            return None
    legacy_token = db.setting_get("google_token_path")
    if legacy_token and Path(legacy_token).exists():
        return Credentials.from_authorized_user_file(legacy_token, SCOPES)
    return None


def save_credentials(creds):
    db.setting_set("google_token_json", creds.to_json())


def google_auth_status():
    creds = load_saved_credentials()
    return {
        "authorized": bool(creds and creds.valid),
        "expired": bool(creds and creds.expired) if creds else False,
        "has_refresh_token": bool(creds and creds.refresh_token) if creds else False,
        "credentials_file": str(bundled_credentials_path()),
        "credentials_file_exists": bundled_credentials_path().exists(),
    }


def run_google_oauth():
    credentials_file = bundled_credentials_path()
    if not credentials_file.exists():
        raise SheetWriteError(
            "Google授权未配置",
            f"Missing Google OAuth client file: {credentials_file}",
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
    creds = flow.run_local_server(port=0)
    save_credentials(creds)
    return creds


def clear_google_oauth():
    db.setting_set("google_token_json", "")


def is_retryable_sheet_error(exc):
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return True
    if isinstance(exc, HttpError):
        return exc.resp.status in {429, 500, 502, 503, 504}
    text = repr(exc).lower()
    return any(marker in text for marker in ["timed out", "timeout", "temporarily unavailable", "connection reset"])


def readable_sheet_error(action_message, exc):
    text = repr(exc).lower()
    if isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in text or "timeout" in text:
        return f"{action_message}：Google 表格连接超时，请检查网络后重试"
    if isinstance(exc, HttpError):
        if exc.resp.status == 429:
            return f"{action_message}：Google 表格请求太频繁，请稍后重试"
        if exc.resp.status in {500, 502, 503, 504}:
            return f"{action_message}：Google 表格服务暂时不可用，请稍后重试"
    return action_message
