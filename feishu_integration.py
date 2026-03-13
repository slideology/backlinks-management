import json
import logging
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import requests
from sheet_localization import FEISHU_HEADERS_ZH, GOOGLE_HEADERS


logger = logging.getLogger(__name__)

DEFAULT_HEADERS = [
    "Execution Time",
    "Google Sheets Row",
    "Target URL",
    "Status",
    "Failure Reason",
    "Comment Format",
    "Target Website",
    "Batch Token",
    "Used Vision",
    "Diagnostic Category",
]

BACKLINK_COLUMNS = GOOGLE_HEADERS
BACKLINK_HEADERS_ZH = FEISHU_HEADERS_ZH


def load_feishu_config(config_path: str = "config.json") -> dict:
    defaults = {
        "enabled": False,
        "app_id": "",
        "app_secret": "",
        "spreadsheet_token": "",
        "sheet_id": "",
        "auth_mode": "app",
        "redirect_uri": "http://127.0.0.1:8787/callback",
        "user_token_file": ".feishu_user_token.json",
        "scopes": [
            "offline_access",
            "sheets:spreadsheet",
            "sheets:spreadsheet:readonly",
        ],
    }
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        merged = {**defaults, **config.get("feishu", {})}
        if isinstance(merged.get("scopes"), str):
            merged["scopes"] = merged["scopes"].split()
        return merged
    except Exception as exc:
        logger.warning("读取飞书配置失败: %s", exc)
        return defaults


class FeishuClient:
    def __init__(
        self,
        app_id: str,
        app_secret: str,
        spreadsheet_token: str,
        sheet_id: str,
        auth_mode: str = "app",
        redirect_uri: str = "http://127.0.0.1:8787/callback",
        user_token_file: str = ".feishu_user_token.json",
        scopes: Optional[list[str]] = None,
        timeout: int = 10,
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.spreadsheet_token = spreadsheet_token
        self.sheet_id = sheet_id
        self.auth_mode = auth_mode
        self.redirect_uri = redirect_uri
        self.user_token_file = user_token_file
        self.scopes = scopes or ["offline_access", "sheets:spreadsheet", "sheets:spreadsheet:readonly"]
        self.timeout = timeout
        self._tenant_access_token: Optional[str] = None
        self._app_access_token: Optional[str] = None

    @classmethod
    def from_config(cls, config_path: str = "config.json") -> Optional["FeishuClient"]:
        config = load_feishu_config(config_path)
        required = ("app_id", "app_secret")
        if not config.get("enabled"):
            return None
        if any(not config.get(key) for key in required):
            logger.warning("飞书已启用，但应用凭证不完整。")
            return None
        return cls(
            app_id=config["app_id"],
            app_secret=config["app_secret"],
            spreadsheet_token=config.get("spreadsheet_token", ""),
            sheet_id=config.get("sheet_id", ""),
            auth_mode=config.get("auth_mode", "app"),
            redirect_uri=config.get("redirect_uri", "http://127.0.0.1:8787/callback"),
            user_token_file=config.get("user_token_file", ".feishu_user_token.json"),
            scopes=config.get("scopes"),
        )

    def get_authorization_url(self, state: Optional[str] = None) -> tuple[str, str]:
        actual_state = state or secrets.token_urlsafe(24)
        params = {
            "app_id": self.app_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "state": actual_state,
        }
        return (
            f"https://accounts.feishu.cn/open-apis/authen/v1/authorize?{urlencode(params)}",
            actual_state,
        )

    def token_path(self) -> Path:
        return Path(self.user_token_file)

    def save_user_token(self, payload: dict) -> None:
        token_data = {
            "access_token": payload["access_token"],
            "refresh_token": payload.get("refresh_token", ""),
            "expires_at": int(time.time()) + int(payload.get("expires_in", 0)),
            "refresh_expires_at": int(time.time()) + int(payload.get("refresh_expires_in", 0)),
            "token_type": payload.get("token_type", "Bearer"),
        }
        self.token_path().write_text(json.dumps(token_data, indent=2, ensure_ascii=False), encoding="utf-8")

    def load_user_token(self) -> Optional[dict]:
        path = self.token_path()
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def get_tenant_access_token(self) -> str:
        if self._tenant_access_token:
            return self._tenant_access_token

        response = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取飞书 tenant_access_token 失败: {data}")

        self._tenant_access_token = data["tenant_access_token"]
        return self._tenant_access_token

    def get_app_access_token(self) -> str:
        if self._app_access_token:
            return self._app_access_token

        response = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal/",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取飞书 app_access_token 失败: {data}")
        self._app_access_token = data["app_access_token"]
        return self._app_access_token

    def exchange_code_for_user_token(self, code: str) -> dict:
        app_access_token = self.get_app_access_token()
        response = requests.post(
            "https://open.feishu.cn/open-apis/authen/v1/access_token",
            headers={
                "Authorization": f"Bearer {app_access_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "grant_type": "authorization_code",
                "code": code,
                "app_id": self.app_id,
                "app_secret": self.app_secret,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"获取飞书 user_access_token 失败: {data}")
        token_payload = data.get("data", {})
        self.save_user_token(token_payload)
        return token_payload

    def refresh_user_access_token(self, refresh_token: str) -> dict:
        app_access_token = self.get_app_access_token()
        response = requests.post(
            "https://open.feishu.cn/open-apis/authen/v1/refresh_access_token",
            headers={
                "Authorization": f"Bearer {app_access_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "app_id": self.app_id,
                "app_secret": self.app_secret,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"刷新飞书 user_access_token 失败: {data}")
        token_payload = data.get("data", {})
        self.save_user_token(token_payload)
        return token_payload

    def get_user_access_token(self) -> str:
        token_data = self.load_user_token()
        if not token_data:
            raise RuntimeError("未找到飞书用户授权，请先运行 setup_feishu_user_auth.py")

        if token_data.get("expires_at", 0) - 60 > time.time():
            return token_data["access_token"]

        refresh_token = token_data.get("refresh_token", "")
        if not refresh_token:
            raise RuntimeError("飞书用户 token 已过期且缺少 refresh_token，请重新授权。")

        refreshed = self.refresh_user_access_token(refresh_token)
        return refreshed["access_token"]

    def build_headers(self, as_user: Optional[bool] = None) -> dict:
        use_user = self.auth_mode == "user" if as_user is None else as_user
        token = self.get_user_access_token() if use_user else self.get_tenant_access_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def read_range(self, value_range: str) -> list[list[str]]:
        encoded_range = requests.utils.quote(value_range, safe="")
        response = requests.get(
            f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{self.spreadsheet_token}/values/{encoded_range}",
            headers=self.build_headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"读取飞书表格失败: {data}")

        return data.get("data", {}).get("valueRange", {}).get("values", []) or []

    def write_range(self, value_range: str, values: list[list[str]]) -> None:
        response = requests.put(
            f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{self.spreadsheet_token}/values",
            headers=self.build_headers(),
            json={"valueRange": {"range": value_range, "values": values}},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"写入飞书表格失败: {data}")

    def create_spreadsheet(self, title: str, as_user: Optional[bool] = None) -> dict:
        response = requests.post(
            "https://open.feishu.cn/open-apis/sheets/v3/spreadsheets",
            headers=self.build_headers(as_user=as_user),
            json={"title": title},
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"创建飞书表格失败: {data}")
        return data.get("data", {}).get("spreadsheet", {})

    def get_sheet_id_by_token(self, spreadsheet_token: str, as_user: Optional[bool] = None) -> str:
        response = requests.get(
            f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/metainfo",
            headers=self.build_headers(as_user=as_user),
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("code") != 0:
            raise RuntimeError(f"读取飞书表格元信息失败: {data}")
        sheets = data.get("data", {}).get("sheets", [])
        if not sheets:
            raise RuntimeError("飞书表格中未找到任何 sheet。")
        return sheets[0]["sheetId"]

    def attach_spreadsheet(self, spreadsheet_token: str, sheet_id: str) -> None:
        self.spreadsheet_token = spreadsheet_token
        self.sheet_id = sheet_id

    def ensure_headers(self) -> list[str]:
        values = self.read_range(f"{self.sheet_id}!A1:J2")
        if not values or not values[0] or not any(cell is not None and str(cell).strip() for cell in values[0]):
            self.write_range(f"{self.sheet_id}!A1:J1", [DEFAULT_HEADERS])
            return DEFAULT_HEADERS

        headers = values[0]
        missing_headers = [header for header in DEFAULT_HEADERS if header not in headers]
        if missing_headers:
            raise RuntimeError(f"飞书表格缺少表头: {', '.join(missing_headers)}")
        return headers

    def ensure_backlink_headers(self) -> list[str]:
        values = self.read_range(f"{self.sheet_id}!A1:S2")
        if not values or not values[0] or not any(cell is not None and str(cell).strip() for cell in values[0]):
            self.write_range(f"{self.sheet_id}!A1:S1", [BACKLINK_HEADERS_ZH])
            return BACKLINK_HEADERS_ZH

        headers = [str(cell or "") for cell in values[0]]
        if headers[: len(BACKLINK_HEADERS_ZH)] != BACKLINK_HEADERS_ZH:
            self.write_range(f"{self.sheet_id}!A1:S1", [BACKLINK_HEADERS_ZH])
            return BACKLINK_HEADERS_ZH
        return headers

    def overwrite_backlink_rows(self, rows: list[list[str]]) -> int:
        self.ensure_backlink_headers()
        values = [BACKLINK_HEADERS_ZH] + rows
        end_row = len(values)
        self.write_range(f"{self.sheet_id}!A1:S{end_row}", values)
        return end_row

    def upsert_backlink_row(self, google_sheet_row: int, row_values: list[str]) -> int:
        self.ensure_backlink_headers()
        target_row_index = max(2, int(google_sheet_row))
        self.write_range(f"{self.sheet_id}!A{target_row_index}:S{target_row_index}", [row_values])
        return target_row_index

    def upsert_execution_record(self, record: dict) -> int:
        headers = self.ensure_headers()
        rows = self.read_range(f"{self.sheet_id}!A1:J5000")
        header_map = {name: idx for idx, name in enumerate(headers)}
        google_row_value = str(record.get("Google Sheets Row", ""))

        target_row_index = None
        for offset, row in enumerate(rows[1:], start=2):
            current_value = row[header_map["Google Sheets Row"]] if len(row) > header_map["Google Sheets Row"] else ""
            if current_value == google_row_value:
                target_row_index = offset
                break

        if target_row_index is None:
            non_empty_rows = 1
            for offset, row in enumerate(rows[1:], start=2):
                if any(cell is not None and str(cell).strip() for cell in row):
                    non_empty_rows = offset
            target_row_index = non_empty_rows + 1

        values = [str(record.get(header, "")) for header in headers]
        self.write_range(f"{self.sheet_id}!A{target_row_index}:J{target_row_index}", [values])
        return target_row_index


def create_feishu_client(config_path: str = "config.json") -> Optional[FeishuClient]:
    try:
        return FeishuClient.from_config(config_path)
    except Exception as exc:
        logger.warning("初始化飞书客户端失败: %s", exc)
        return None


def build_execution_record(result: dict) -> dict:
    return {
        "Execution Time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "Google Sheets Row": result.get("google_sheets_row", ""),
        "Target URL": result.get("url", ""),
        "Status": "completed" if result.get("success") else "failed",
        "Failure Reason": result.get("reason", ""),
        "Comment Format": result.get("format", ""),
        "Target Website": result.get("target_website", ""),
        "Batch Token": result.get("batch_token", ""),
        "Used Vision": "yes" if result.get("used_vision") else "no",
        "Diagnostic Category": result.get("diagnostic_category", ""),
    }
