from __future__ import annotations

from typing import Optional

from backlink_state import (
    LEGACY_HISTORY_HEADERS,
    LEGACY_SOURCE_LIBRARY_HEADERS,
    SOURCE_MASTER_BASE_HEADERS,
    STATUS_HEADERS,
    TARGET_SITE_HEADERS,
    build_legacy_history_rows,
    build_legacy_source_library_rows,
    build_source_master_rows,
    build_target_site_rows,
    dynamic_source_headers,
    load_targets,
    migrate_old_record_rows,
    reconcile_status_rows,
)
from feishu_workbook import FeishuWorkbook
from legacy_feishu_history import LegacyFeishuHistoryStore, load_legacy_history_config


def _read_existing_targets(workbook: FeishuWorkbook) -> list[dict]:
    _, rows = workbook.read_sheet_dicts("targets", max_cols=len(TARGET_SITE_HEADERS))
    return rows


def _read_existing_source_rows(workbook: FeishuWorkbook, max_cols: int = 200) -> list[dict]:
    headers = workbook.read_sheet_headers("sources", max_cols=max_cols)
    if not headers:
        return []

    rows = []
    for _, row in workbook.iter_sheet_dict_rows(
        "sources",
        max_cols=max_cols,
        page_size=250,
        headers=headers,
    ):
        rows.append(row)
    return rows


def _read_existing_status_rows(workbook: FeishuWorkbook) -> tuple[list[dict], list[dict]]:
    headers, rows = workbook.read_sheet_dicts("records", max_cols=max(len(STATUS_HEADERS), 22))
    header_set = set(headers)
    if set(STATUS_HEADERS).issubset(header_set):
        return rows, []
    if {"来源链接", "目标网站", "状态"}.issubset(header_set):
        return [], rows
    return [], []


def sync_reporting_workbook(workbook: Optional[FeishuWorkbook] = None):
    workbook = workbook or FeishuWorkbook.from_config()
    if not workbook:
        raise RuntimeError("飞书未正确配置，无法同步运营总表。")

    history_store = LegacyFeishuHistoryStore.from_config()
    legacy_config = load_legacy_history_config()

    existing_target_rows = _read_existing_targets(workbook)
    target_rows = build_target_site_rows(
        existing_rows=existing_target_rows,
        bootstrap_targets=load_targets(),
        promoted_site_map=legacy_config.get("promoted_site_map"),
    )
    existing_source_rows = _read_existing_source_rows(workbook, max_cols=max(len(dynamic_source_headers(target_rows)), 200))
    existing_status_rows, old_record_rows = _read_existing_status_rows(workbook)
    migrated_rows = migrate_old_record_rows(
        old_record_rows,
        target_rows,
        promoted_site_map=legacy_config.get("promoted_site_map"),
    )
    history_rows = build_legacy_history_rows(history_store)
    library_rows = build_legacy_source_library_rows(history_store)
    status_rows = reconcile_status_rows(
        existing_status_rows=existing_status_rows or migrated_rows,
        target_rows=target_rows,
        library_rows=library_rows,
        legacy_history_rows=history_rows,
        promoted_site_map=legacy_config.get("promoted_site_map"),
    )
    source_rows = build_source_master_rows(status_rows, target_rows, existing_source_rows=existing_source_rows)

    written = {
        "来源主表": workbook.overwrite_sheet_dicts("sources", dynamic_source_headers(target_rows), source_rows),
        "站点发布状态表": workbook.overwrite_sheet_dicts("records", STATUS_HEADERS, status_rows),
        "目标站表": workbook.overwrite_sheet_dicts("targets", TARGET_SITE_HEADERS, target_rows),
        "旧表历史事实表": workbook.overwrite_sheet_dicts("history", LEGACY_HISTORY_HEADERS, history_rows),
        "旧表全量来源库": workbook.overwrite_sheet_dicts("library", LEGACY_SOURCE_LIBRARY_HEADERS, library_rows),
    }

    return {
        "spreadsheet_token": workbook.spreadsheet_token,
        "spreadsheet_url": workbook.spreadsheet_url,
        "sheet_ids": workbook.sheet_ids,
        "written": written,
        "targets": target_rows,
        "status_rows": status_rows,
        "source_rows": source_rows,
    }


def main():
    result = sync_reporting_workbook()
    print("✅ 飞书运营总表同步完成")
    print(f"   工作簿: {result['spreadsheet_url']}")
    for sheet_name, row_count in result["written"].items():
        print(f"   {sheet_name}: 写入 {row_count - 1} 条数据")


if __name__ == "__main__":
    main()
