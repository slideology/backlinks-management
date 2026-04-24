import json
import logging
import time
from pathlib import Path
from typing import Iterator, Optional

from feishu_integration import FeishuClient, _column_letter, create_feishu_client
from legacy_feishu_history import extract_cell_text, extract_cell_url, normalize_source_url

logger = logging.getLogger(__name__)


def load_reporting_config(config_path: str = "config.json") -> dict:
    defaults = {
        "enabled": True,
        "workbook_title": "外链运营总表",
        "state_file": "artifacts/reporting_workbook/state.json",
        "write_buffer_file": "artifacts/reporting_workbook/write_buffer.json",
        "flush_buffer_limit": 20,
        "excluded_source_domains": [],
        "excluded_source_urls": [],
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
        workbook = cls(client, config, spreadsheet_token, sheet_ids, spreadsheet_url)
        try:
            workbook.flush_buffered_writes(limit=int(config.get("flush_buffer_limit", 20) or 20))
        except Exception as exc:
            logger.warning("补写飞书缓冲队列失败，稍后重试: %s", exc)
        return workbook

    def sheet_id(self, key: str) -> str:
        return self.sheet_ids[key]

    def _buffer_path(self) -> Path:
        return Path(self.config.get("write_buffer_file", "artifacts/reporting_workbook/write_buffer.json"))

    def _load_buffer(self) -> list[dict]:
        path = self._buffer_path()
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_buffer(self, items: list[dict]) -> None:
        path = self._buffer_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

    def _buffer_identity(self, item: dict) -> tuple:
        op = item.get("op", "")
        key = item.get("key", "")
        if op == "upsert":
            row = item.get("row", {}) or {}
            key_fields = tuple(item.get("key_fields", []) or [])
            normalized = tuple(self._normalize_key_field(field, row.get(field, "")) for field in key_fields)
            return op, key, key_fields, normalized
        if op == "partial_row":
            return op, key, int(item.get("row_index", 0) or 0)
        if op == "write_row":
            return op, key, int(item.get("row_index", 0) or 0)
        return op, key

    def _enqueue_write(self, item: dict) -> None:
        queued = self._load_buffer()
        item["buffered_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        identity = self._buffer_identity(item)
        replaced = False
        for index, existing in enumerate(queued):
            if self._buffer_identity(existing) == identity:
                queued[index] = item
                replaced = True
                break
        if not replaced:
            queued.append(item)
        self._save_buffer(queued)

    def _apply_buffered_write(self, item: dict) -> int:
        op = item.get("op", "")
        if op == "overwrite":
            return self._overwrite_sheet_dicts_now(item["key"], item["headers"], item["rows"])
        if op == "upsert":
            return self._upsert_sheet_dict_now(
                item["key"], item["headers"], item["key_fields"], item["row"], max_rows=int(item.get("max_rows", 50000))
            )
        if op == "partial_row":
            return self._write_sheet_partial_row_now(item["key"], item["header_row"], int(item["row_index"]), item["updates"])
        if op == "write_row":
            return self._write_sheet_row_now(item["key"], item["headers"], int(item["row_index"]), item["row"])
        raise RuntimeError(f"未知的飞书缓冲操作: {op}")

    def flush_buffered_writes(self, limit: int = 20) -> int:
        queued = self._load_buffer()
        if not queued:
            return 0

        flushed = 0
        remaining = queued
        for index, item in enumerate(queued[: max(0, limit)]):
            try:
                self._apply_buffered_write(item)
                flushed += 1
                remaining = queued[index + 1 :]
            except Exception as exc:
                logger.warning("飞书缓冲补写失败，保留队列稍后重试: %s", exc)
                remaining = queued[index:]
                break

        self._save_buffer(remaining)
        return flushed

    def read_sheet_values(self, key: str, max_cols: int, max_rows: int = 50000) -> list[list]:
        last_col = _column_letter(max_cols)
        sheet_id = self.sheet_id(key)
        return self.client.read_range(f"{sheet_id}!A1:{last_col}{max_rows}")

    def read_sheet_headers(self, key: str, max_cols: int) -> list[str]:
        last_col = _column_letter(max_cols)
        sheet_id = self.sheet_id(key)
        values = self.client.read_range(f"{sheet_id}!A1:{last_col}1")
        if not values:
            return []
        return [str(cell or "") for cell in values[0]]

    def ensure_sheet_headers(self, key: str, headers: list[str]) -> None:
        sheet_id = self.sheet_id(key)
        last_col = _column_letter(len(headers))
        existing_headers = self.read_sheet_headers(key, max_cols=len(headers))
        if existing_headers[: len(headers)] != headers:
            self.client.write_range(f"{sheet_id}!A1:{last_col}1", [headers])

    def iter_sheet_dict_rows(
        self,
        key: str,
        max_cols: int,
        page_size: int = 500,
        start_row: int = 2,
        headers: Optional[list[str]] = None,
    ) -> Iterator[tuple[int, dict]]:
        actual_headers = headers or self.read_sheet_headers(key, max_cols=max_cols)
        if not actual_headers:
            return

        last_col = _column_letter(len(actual_headers))
        sheet_id = self.sheet_id(key)
        row_cursor = start_row

        while True:
            row_end = row_cursor + page_size - 1
            values = self.client.read_range(f"{sheet_id}!A{row_cursor}:{last_col}{row_end}")
            if not values:
                break

            saw_non_empty = False
            for offset, raw_row in enumerate(values, start=row_cursor):
                if not any(str(cell or "").strip() for cell in raw_row):
                    continue
                saw_non_empty = True
                yield offset, {
                    actual_headers[i]: raw_row[i] if i < len(raw_row) else ""
                    for i in range(len(actual_headers))
                }

            if len(values) < page_size or not saw_non_empty:
                break
            row_cursor += page_size

    def iter_sheet_selected_rows(
        self,
        key: str,
        selected_headers: list[str],
        max_cols: int = 250,
        page_size: int = 500,
        start_row: int = 2,
    ) -> Iterator[tuple[int, dict]]:
        actual_headers = self.read_sheet_headers(key, max_cols=max_cols)
        if not actual_headers:
            return

        header_positions = {}
        for header in selected_headers:
            try:
                header_positions[header] = actual_headers.index(header)
            except ValueError:
                header_positions[header] = None

        present_positions = [pos for pos in header_positions.values() if pos is not None]
        if not present_positions:
            return

        last_col = _column_letter(max(present_positions) + 1)
        sheet_id = self.sheet_id(key)
        row_cursor = start_row

        while True:
            row_end = row_cursor + page_size - 1
            values = self.client.read_range(f"{sheet_id}!A{row_cursor}:{last_col}{row_end}")
            if not values:
                break

            saw_non_empty = False
            for offset, raw_row in enumerate(values, start=row_cursor):
                row = {}
                for header, pos in header_positions.items():
                    row[header] = raw_row[pos] if pos is not None and pos < len(raw_row) else ""
                if not any(str(cell or "").strip() for cell in row.values()):
                    continue
                saw_non_empty = True
                yield offset, row

            if len(values) < page_size or not saw_non_empty:
                break
            row_cursor += page_size

    def _write_sheet_row_now(self, key: str, headers: list[str], row_index: int, row: dict) -> int:
        sheet_id = self.sheet_id(key)
        last_col = _column_letter(len(headers))
        ordered = [row.get(header, "") for header in headers]
        self.client.write_range(f"{sheet_id}!A{row_index}:{last_col}{row_index}", [ordered])
        return row_index

    def write_sheet_row(self, key: str, headers: list[str], row_index: int, row: dict) -> int:
        try:
            return self._write_sheet_row_now(key, headers, row_index, row)
        except Exception as exc:
            logger.warning("飞书写整行失败，已写入本地缓冲: %s", exc)
            self._enqueue_write(
                {"op": "write_row", "key": key, "headers": headers, "row_index": row_index, "row": row, "reason": str(exc)}
            )
            return row_index

    def _write_sheet_partial_row_now(self, key: str, header_row: list[str], row_index: int, updates: dict) -> int:
        sheet_id = self.sheet_id(key)
        indexed_updates = []
        for field, value in updates.items():
            if field not in header_row:
                continue
            indexed_updates.append((header_row.index(field), value))

        if not indexed_updates:
            return row_index

        indexed_updates.sort(key=lambda item: item[0])
        run_start = indexed_updates[0][0]
        run_values = [indexed_updates[0][1]]
        previous_index = indexed_updates[0][0]

        for current_index, value in indexed_updates[1:]:
            if current_index == previous_index + 1:
                run_values.append(value)
            else:
                start_col = _column_letter(run_start + 1)
                end_col = _column_letter(previous_index + 1)
                self.client.write_range(
                    f"{sheet_id}!{start_col}{row_index}:{end_col}{row_index}",
                    [run_values],
                )
                run_start = current_index
                run_values = [value]
            previous_index = current_index

        start_col = _column_letter(run_start + 1)
        end_col = _column_letter(previous_index + 1)
        self.client.write_range(
            f"{sheet_id}!{start_col}{row_index}:{end_col}{row_index}",
            [run_values],
        )
        return row_index

    def write_sheet_partial_row(self, key: str, header_row: list[str], row_index: int, updates: dict) -> int:
        try:
            return self._write_sheet_partial_row_now(key, header_row, row_index, updates)
        except Exception as exc:
            logger.warning("飞书部分写行失败，已写入本地缓冲: %s", exc)
            self._enqueue_write(
                {
                    "op": "partial_row",
                    "key": key,
                    "header_row": header_row,
                    "row_index": row_index,
                    "updates": updates,
                    "reason": str(exc),
                }
            )
            return row_index

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

    def _overwrite_sheet_dicts_now(self, key: str, headers: list[str], rows: list[dict]) -> int:
        values = []
        for row in rows:
            values.append([row.get(header, "") for header in headers])
        return self.client.overwrite_sheet_rows(self.sheet_id(key), headers, values)

    def overwrite_sheet_dicts(self, key: str, headers: list[str], rows: list[dict]) -> int:
        try:
            return self._overwrite_sheet_dicts_now(key, headers, rows)
        except Exception as exc:
            logger.warning("飞书整表覆盖失败，已写入本地缓冲: %s", exc)
            self._enqueue_write(
                {"op": "overwrite", "key": key, "headers": headers, "rows": rows, "reason": str(exc)}
            )
            return len(rows)

    @staticmethod
    def _normalize_key_field(field: str, value):
        if field in {"来源链接", "目标网站", "成功链接"}:
            normalized = normalize_source_url(extract_cell_url(value) or extract_cell_text(value))
            return normalized or str(extract_cell_text(value) or "").strip()
        return str(extract_cell_text(value) or value or "").strip()

    @classmethod
    def _normalize_compare_field(cls, field: str, value) -> str:
        if field in {"来源链接", "目标网站", "成功链接"}:
            return cls._normalize_key_field(field, value)
        return str(extract_cell_text(value) or value or "").strip()

    def _upsert_sheet_dict_now(self, key: str, headers: list[str], key_fields: list[str], row: dict, max_rows: int = 50000) -> int:
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
            if all(
                self._normalize_key_field(field, row_dict.get(field, "")) == self._normalize_key_field(field, row.get(field, ""))
                for field in key_fields
            ):
                target_row_index = offset
                break

        if target_row_index is None:
            target_row_index = last_non_empty + 1

        ordered = [row.get(header, "") for header in headers]
        self.client.write_range(f"{sheet_id}!A{target_row_index}:{last_col}{target_row_index}", [ordered])
        return target_row_index

    def upsert_sheet_dict(self, key: str, headers: list[str], key_fields: list[str], row: dict, max_rows: int = 50000) -> int:
        try:
            return self._upsert_sheet_dict_now(key, headers, key_fields, row, max_rows=max_rows)
        except Exception as exc:
            logger.warning("飞书 upsert 失败，已写入本地缓冲: %s", exc)
            self._enqueue_write(
                {
                    "op": "upsert",
                    "key": key,
                    "headers": headers,
                    "key_fields": key_fields,
                    "row": row,
                    "max_rows": max_rows,
                    "reason": str(exc),
                }
            )
            return 0

    def upsert_status_row(self, row: dict, max_rows: int = 50000) -> int:
        """
        Backward-compatible wrapper for older call sites that still expect a
        dedicated status-row helper.
        """
        from backlink_state import STATUS_HEADERS

        return self.upsert_sheet_dict(
            "records",
            STATUS_HEADERS,
            ["来源链接", "目标站标识"],
            row,
            max_rows=max_rows,
        )

    def _sync_sheet_dicts_now(
        self,
        key: str,
        headers: list[str],
        key_fields: list[str],
        rows: list[dict],
        max_rows: int = 50000,
        prune_stale: bool = True,
    ) -> int:
        sheet_id = self.sheet_id(key)
        last_col = _column_letter(len(headers))
        existing_nonempty_rows = self.client.count_nonempty_rows(sheet_id, max_rows=max_rows)
        row_limit = max(1, existing_nonempty_rows)
        values = self.client.read_range(f"{sheet_id}!A1:{last_col}{row_limit}") if existing_nonempty_rows else []

        if not values or [str(cell or "") for cell in values[0][: len(headers)]] != headers:
            self.client.write_range(f"{sheet_id}!A1:{last_col}1", [headers])
            values = [headers]
            existing_nonempty_rows = 1

        existing_rows_by_index: dict[int, dict] = {}
        existing_index_by_key: dict[tuple, int] = {}
        last_nonempty = 1

        for offset, raw_row in enumerate(values[1:], start=2):
            row_dict = {headers[i]: raw_row[i] if i < len(raw_row) else "" for i in range(len(headers))}
            if any(str(extract_cell_text(cell) or cell or "").strip() for cell in raw_row):
                last_nonempty = offset
                existing_rows_by_index[offset] = row_dict
                normalized_key = tuple(self._normalize_key_field(field, row_dict.get(field, "")) for field in key_fields)
                if all(normalized_key):
                    existing_index_by_key[normalized_key] = offset

        blank_row = {header: "" for header in headers}
        seen_existing_indices: set[int] = set()
        next_row_index = last_nonempty + 1

        for row in rows:
            normalized_key = tuple(self._normalize_key_field(field, row.get(field, "")) for field in key_fields)
            existing_row_index = existing_index_by_key.get(normalized_key)
            if existing_row_index:
                seen_existing_indices.add(existing_row_index)
                existing_row = existing_rows_by_index.get(existing_row_index, {})
                updates = {}
                for header in headers:
                    existing_value = self._normalize_compare_field(header, existing_row.get(header, ""))
                    new_value = self._normalize_compare_field(header, row.get(header, ""))
                    if existing_value != new_value:
                        updates[header] = row.get(header, "")
                if updates:
                    self._write_sheet_partial_row_now(key, headers, existing_row_index, updates)
                continue

            self._write_sheet_row_now(key, headers, next_row_index, row)
            next_row_index += 1

        if prune_stale:
            for row_index in sorted(existing_rows_by_index):
                if row_index in seen_existing_indices:
                    continue
                self._write_sheet_row_now(key, headers, row_index, blank_row)

        target_row_count = max(2, max(next_row_index - 1, len(rows) + 1))
        try:
            self.client.resize_sheet(
                sheet_id,
                row_count=target_row_count,
                column_count=len(headers),
                spreadsheet_token=self.spreadsheet_token,
                as_user=True,
                frozen_row_count=1,
            )
        except Exception as exc:
            logger.warning("飞书 resize 失败，忽略并继续: %s", exc)

        return len(rows) + 1

    def sync_sheet_dicts(
        self,
        key: str,
        headers: list[str],
        key_fields: list[str],
        rows: list[dict],
        max_rows: int = 50000,
        prune_stale: bool = True,
    ) -> int:
        try:
            return self._sync_sheet_dicts_now(
                key,
                headers,
                key_fields,
                rows,
                max_rows=max_rows,
                prune_stale=prune_stale,
            )
        except Exception as exc:
            logger.warning("飞书增量同步失败，回退整表覆盖: %s", exc)
            return self.overwrite_sheet_dicts(key, headers, rows)
