import json
from pathlib import Path
from typing import Optional

from feishu_integration import FeishuClient, _column_letter, create_feishu_client


def load_reporting_config(config_path: str = "config.json") -> dict:
    defaults = {
        "enabled": True,
        "workbook_title": "外链运营总表",
        "state_file": "artifacts/reporting_workbook/state.json",
        "sheet_titles": {
            "sources": "来源主表",
            "records": "站点发布状态表",
            "targets": "目标站表",
            "history": "旧表历史事实表",
            "library": "旧表全量来源库",
        },
    }
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        merged = {**defaults, **config.get("reporting_workbook", {})}
        merged["sheet_titles"] = {
            **defaults["sheet_titles"],
            **config.get("reporting_workbook", {}).get("sheet_titles", {}),
        }
        return merged
    except Exception:
        return defaults


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_reporting_workbook(client: FeishuClient, config: dict) -> tuple[str, dict, str]:
    state_path = Path(config["state_file"])
    state = load_state(state_path)
    spreadsheet_token = state.get("spreadsheet_token", "")
    spreadsheet_url = state.get("spreadsheet_url", "")

    if not spreadsheet_token:
        spreadsheet = client.create_spreadsheet(config["workbook_title"], as_user=True)
        spreadsheet_token = spreadsheet["spreadsheet_token"]
        spreadsheet_url = spreadsheet.get("url", "")
        first_sheet_id = client.get_sheet_id_by_token(spreadsheet_token, as_user=True)
        client.rename_sheet(
            first_sheet_id,
            config["sheet_titles"]["sources"],
            spreadsheet_token=spreadsheet_token,
            as_user=True,
        )
        state = {
            "spreadsheet_token": spreadsheet_token,
            "spreadsheet_url": spreadsheet_url,
        }
        save_state(state_path, state)

    sheet_titles = config["sheet_titles"]
    known_ids = state.get("sheet_ids", {})
    sheet_ids = {}
    for key, title in sheet_titles.items():
        existing_id = known_ids.get(key)
        if existing_id:
            try:
                client.rename_sheet(existing_id, title, spreadsheet_token=spreadsheet_token, as_user=True)
                sheet_ids[key] = existing_id
                continue
            except Exception:
                pass

        matched_by_title = client.get_sheet_id_by_title(title, spreadsheet_token=spreadsheet_token, as_user=True)
        if matched_by_title:
            sheet_ids[key] = matched_by_title
            continue

        sheet_ids[key] = client.ensure_sheet(title, spreadsheet_token=spreadsheet_token, as_user=True)

    state["sheet_ids"] = sheet_ids
    save_state(state_path, state)
    return spreadsheet_token, sheet_ids, spreadsheet_url


class FeishuWorkbook:
    def __init__(self, client: FeishuClient, config: dict, spreadsheet_token: str, sheet_ids: dict, spreadsheet_url: str):
        self.client = client
        self.config = config
        self.spreadsheet_token = spreadsheet_token
        self.sheet_ids = sheet_ids
        self.spreadsheet_url = spreadsheet_url
        self.client.spreadsheet_token = spreadsheet_token

    @classmethod
    def from_config(cls, config_path: str = "config.json") -> Optional["FeishuWorkbook"]:
        config = load_reporting_config(config_path)
        if not config.get("enabled"):
            return None

        client = create_feishu_client(config_path)
        if not client:
            return None

        spreadsheet_token, sheet_ids, spreadsheet_url = ensure_reporting_workbook(client, config)
        return cls(client, config, spreadsheet_token, sheet_ids, spreadsheet_url)

    def sheet_id(self, key: str) -> str:
        return self.sheet_ids[key]

    def read_sheet_values(self, key: str, max_cols: int, max_rows: int = 50000) -> list[list]:
        last_col = _column_letter(max_cols)
        sheet_id = self.sheet_id(key)
        return self.client.read_range(f"{sheet_id}!A1:{last_col}{max_rows}")

    def read_sheet_dicts(self, key: str, max_cols: int, max_rows: int = 50000) -> tuple[list[str], list[dict]]:
        values = self.read_sheet_values(key, max_cols=max_cols, max_rows=max_rows)
        if not values:
            return [], []
        headers = [str(cell or "") for cell in values[0]]
        rows = []
        for raw_row in values[1:]:
            if not any(str(cell or "").strip() for cell in raw_row):
                continue
            rows.append({headers[i]: raw_row[i] if i < len(raw_row) else "" for i in range(len(headers))})
        return headers, rows

    def overwrite_sheet_dicts(self, key: str, headers: list[str], rows: list[dict]) -> int:
        values = []
        for row in rows:
            values.append([row.get(header, "") for header in headers])
        return self.client.overwrite_sheet_rows(self.sheet_id(key), headers, values)

    def upsert_sheet_dict(self, key: str, headers: list[str], key_fields: list[str], row: dict, max_rows: int = 50000) -> int:
        sheet_id = self.sheet_id(key)
        last_col = _column_letter(len(headers))
        values = self.client.read_range(f"{sheet_id}!A1:{last_col}{max_rows}")
        if not values or [str(cell or "") for cell in values[0][: len(headers)]] != headers:
            self.client.write_range(f"{sheet_id}!A1:{last_col}1", [headers])
            values = [headers]

        target_row_index = None
        last_non_empty = 1
        for offset, raw_row in enumerate(values[1:], start=2):
            if any(str(cell or "").strip() for cell in raw_row):
                last_non_empty = offset
            row_dict = {headers[i]: raw_row[i] if i < len(raw_row) else "" for i in range(len(headers))}
            if all(str(row_dict.get(field, "") or "") == str(row.get(field, "") or "") for field in key_fields):
                target_row_index = offset
                break

        if target_row_index is None:
            target_row_index = last_non_empty + 1

        ordered = [row.get(header, "") for header in headers]
        self.client.write_range(f"{sheet_id}!A{target_row_index}:{last_col}{target_row_index}", [ordered])
        return target_row_index
