from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

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
from legacy_feishu_history import (
    LegacyFeishuHistoryStore,
    extract_cell_text,
    extract_cell_url,
    load_legacy_history_config,
    normalize_source_url,
)


def _normalize_excluded_domains(domains: list[str] | tuple[str, ...] | None) -> set[str]:
    normalized = set()
    for domain in domains or []:
        text = str(domain or "").strip().lower()
        if text:
            normalized.add(text.lstrip("."))
    return normalized


def _normalize_excluded_urls(urls: list[str] | tuple[str, ...] | None) -> set[str]:
    normalized = set()
    for url in urls or []:
        text = normalize_source_url(str(url or "").strip())
        if text:
            normalized.add(text)
    return normalized


def _row_source_url(row: dict) -> str:
    raw_value = row.get("来源链接", "")
    normalized = normalize_source_url(extract_cell_url(raw_value) or extract_cell_text(raw_value))
    if normalized:
        return normalized
    return str(extract_cell_text(raw_value) or raw_value or "").strip()


def _row_matches_exclusions(row: dict, excluded_domains: set[str], excluded_urls: set[str]) -> bool:
    if not excluded_domains and not excluded_urls:
        return False
    source_url = _row_source_url(row)
    if not source_url:
        return False
    if excluded_urls and source_url in excluded_urls:
        return True
    if not excluded_domains:
        return False
    try:
        hostname = (urlparse(source_url).hostname or "").strip().lower()
    except Exception:
        hostname = ""
    if not hostname:
        return False
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in excluded_domains)


def _filter_rows_by_exclusions(rows: list[dict], excluded_domains: set[str], excluded_urls: set[str]) -> list[dict]:
    if not excluded_domains and not excluded_urls:
        return rows
    return [row for row in rows if not _row_matches_exclusions(row, excluded_domains, excluded_urls)]


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


def build_reporting_snapshot(workbook: Optional[FeishuWorkbook] = None) -> dict:
    workbook = workbook or FeishuWorkbook.from_config()
    if not workbook:
        raise RuntimeError("飞书未正确配置，无法同步运营总表。")

    excluded_domains = _normalize_excluded_domains(workbook.config.get("excluded_source_domains", []))
    excluded_urls = _normalize_excluded_urls(workbook.config.get("excluded_source_urls", []))
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
        existing_source_rows=existing_source_rows,
        promoted_site_map=legacy_config.get("promoted_site_map"),
    )
    source_rows = build_source_master_rows(status_rows, target_rows, existing_source_rows=existing_source_rows)

    history_rows = _filter_rows_by_exclusions(history_rows, excluded_domains, excluded_urls)
    library_rows = _filter_rows_by_exclusions(library_rows, excluded_domains, excluded_urls)
    status_rows = _filter_rows_by_exclusions(status_rows, excluded_domains, excluded_urls)
    source_rows = _filter_rows_by_exclusions(source_rows, excluded_domains, excluded_urls)

    return {
        "spreadsheet_token": workbook.spreadsheet_token,
        "spreadsheet_url": workbook.spreadsheet_url,
        "sheet_ids": workbook.sheet_ids,
        "targets": target_rows,
        "status_rows": status_rows,
        "source_rows": source_rows,
        "history_rows": history_rows,
        "library_rows": library_rows,
    }


def sync_reporting_workbook(workbook: Optional[FeishuWorkbook] = None):
    snapshot = build_reporting_snapshot(workbook=workbook)
    workbook = workbook or FeishuWorkbook.from_config()
    if not workbook:
        raise RuntimeError("飞书未正确配置，无法同步运营总表。")

    written = {
        "来源主表": workbook.sync_sheet_dicts("sources", dynamic_source_headers(snapshot["targets"]), ["来源链接"], snapshot["source_rows"]),
        "站点发布状态表": workbook.sync_sheet_dicts("records", STATUS_HEADERS, ["来源链接", "目标站标识"], snapshot["status_rows"]),
        "目标站表": workbook.sync_sheet_dicts("targets", TARGET_SITE_HEADERS, ["站点标识"], snapshot["targets"]),
        "旧表历史事实表": workbook.sync_sheet_dicts("history", LEGACY_HISTORY_HEADERS, ["来源链接", "目标站标识"], snapshot["history_rows"]),
        "旧表全量来源库": workbook.sync_sheet_dicts("library", LEGACY_SOURCE_LIBRARY_HEADERS, ["来源链接"], snapshot["library_rows"]),
    }

    snapshot["written"] = written
    return snapshot


def main():
    result = sync_reporting_workbook()
    print("✅ 飞书运营总表同步完成")
    print(f"   工作簿: {result['spreadsheet_url']}")
    for sheet_name, row_count in result["written"].items():
        print(f"   {sheet_name}: 写入 {row_count - 1} 条数据")


if __name__ == "__main__":
    main()
