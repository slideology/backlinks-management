from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

from backlink_state import TARGET_SITE_HEADERS, dynamic_source_headers
from browser_cdp import DEFAULT_CDP_URL, ensure_allowed_cdp_url, merge_browser_config
from feishu_workbook import FeishuWorkbook
from legacy_feishu_history import extract_cell_text, extract_cell_url, normalize_source_url
from website_format_detector import WebsiteFormatDetector


DEFAULT_CONFIG = {
    "state_file": "artifacts/format_probe/state.json",
    "summary_file": "artifacts/format_probe/summary.json",
    "confidence_threshold": 0.8,
    "enable_vision": True,
    "connect_cdp_url": DEFAULT_CDP_URL,
    "page_load_timeout_ms": 20000,
    "read_page_size": 250,
    "write_retry_count": 5,
    "write_retry_base_seconds": 2,
}


def load_probe_config(config_path: str = "config.json") -> dict:
    try:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_CONFIG)
    merged = {**DEFAULT_CONFIG, **payload.get("format_probe", {})}
    browser_cfg = merge_browser_config(payload.get("browser", {}) or {})
    merged["connect_cdp_url"] = str(browser_cfg.get("connect_cdp_url", "") or merged["connect_cdp_url"])
    merged["browser"] = browser_cfg
    return merged


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_target_rows(workbook: FeishuWorkbook) -> list[dict]:
    _, rows = workbook.read_sheet_dicts("targets", max_cols=len(TARGET_SITE_HEADERS))
    return rows


def read_source_rows_paged(
    workbook: FeishuWorkbook,
    max_cols: int = 250,
    page_size: int = 250,
) -> list[dict]:
    deduped = {}
    for row_index, row in workbook.iter_sheet_selected_rows(
        "sources",
        selected_headers=["来源链接"],
        max_cols=max_cols,
        page_size=page_size,
    ):
        normalized_url = normalize_source_url(
            extract_cell_url(row.get("来源链接", "")) or extract_cell_text(row.get("来源链接", ""))
        )
        if normalized_url:
            deduped[normalized_url] = {
                **row,
                "_row_index": row_index,
            }
    return list(deduped.values())


def open_probe_browser(playwright_manager, cdp_url: str):
    browser_app = playwright_manager.chromium.connect_over_cdp(cdp_url)
    context = browser_app.contexts[0]
    page = context.new_page()
    return browser_app, context, page, True


def should_retry_write(exc: Exception) -> bool:
    message = str(exc).lower()
    return "too many request" in message or "90217" in message


def write_source_row_with_retry(
    workbook: FeishuWorkbook,
    headers: list[str],
    row_index: int,
    row: dict,
    max_retries: int,
    base_sleep: float,
) -> None:
    for attempt in range(max_retries):
        try:
            workbook.write_sheet_partial_row("sources", headers, row_index, row)
            return
        except Exception as exc:
            if attempt >= max_retries - 1 or not should_retry_write(exc):
                raise
            time.sleep(base_sleep * (attempt + 1))


def build_source_probe_update(row: dict, capability: dict, now_text: str) -> dict:
    final_result = capability["final_result"]
    static_result = capability["static_result"]
    return {
        "初始链接格式": static_result.get("recommended_format", "unknown"),
        "最终链接格式": final_result.get("recommended_format", "unknown"),
        "格式检测阶段": capability.get("stage", "static_only"),
        "格式检测证据": final_result.get("evidence_type", "unknown"),
        "格式检测置信度": str(final_result.get("confidence", 0)),
        "是否视觉复核": "是" if capability.get("vision_used") else "否",
        "格式检测时间": now_text,
        "格式检测状态": capability.get("status", "failed"),
    }


def summarize_results(results: list[dict]) -> dict:
    final_formats = Counter()
    upgraded_unknown_to_html = 0
    upgraded_autolink_to_html = 0
    status_counts = Counter()
    for item in results:
        initial_fmt = item["static_result"].get("recommended_format", "unknown")
        final_fmt = item["final_result"].get("recommended_format", "unknown")
        final_formats[final_fmt] += 1
        status_counts[item.get("status", "failed")] += 1
        if initial_fmt == "unknown" and final_fmt == "html":
            upgraded_unknown_to_html += 1
        if initial_fmt == "plain_text_autolink" and final_fmt == "html":
            upgraded_autolink_to_html += 1

    return {
        "total_scanned": len(results),
        "final_format_counts": dict(final_formats),
        "upgraded_unknown_to_html": upgraded_unknown_to_html,
        "upgraded_plain_text_autolink_to_html": upgraded_autolink_to_html,
        "conflict_count": status_counts.get("conflict", 0),
        "failed_count": status_counts.get("failed", 0),
        "completed_count": status_counts.get("completed", 0),
    }


def run_backfill(limit: Optional[int] = None, reset_state: bool = False, config_path: str = "config.json") -> dict:
    config = load_probe_config(config_path)
    state_path = Path(config["state_file"])
    summary_path = Path(config["summary_file"])
    state = {} if reset_state else load_json(state_path, {"processed_urls": [], "results": []})
    processed_urls = set(state.get("processed_urls", []))
    prior_results = list(state.get("results", []))

    workbook = FeishuWorkbook.from_config(config_path)
    if not workbook:
        raise RuntimeError("飞书未正确配置，无法执行来源主表格式回填。")

    target_rows = read_target_rows(workbook)
    source_headers = dynamic_source_headers(target_rows)
    workbook.ensure_sheet_headers("sources", source_headers)
    print("📚 正在分页读取来源主表...")
    source_rows = read_source_rows_paged(
        workbook,
        max_cols=max(len(source_headers), 250),
        page_size=int(config.get("read_page_size", 250)),
    )
    print(f"📦 已加载待检测来源 {len(source_rows)} 条（去重后）")

    detector = WebsiteFormatDetector()
    processed_this_run = []
    scanned = 0

    with sync_playwright() as p:
        cdp_url = ensure_allowed_cdp_url(config["connect_cdp_url"], config.get("browser", {}))
        print(f"🌐 格式回填仅连接 CDP: {cdp_url}")
        browser_owner, _, page, _ = open_probe_browser(p, cdp_url)
        try:
            page.set_default_timeout(int(config["page_load_timeout_ms"]))
            for row in source_rows:
                normalized_url = normalize_source_url(extract_cell_url(row.get("来源链接", "")) or extract_cell_text(row.get("来源链接", "")))
                if not normalized_url or normalized_url in processed_urls:
                    continue
                print(f"🔍 [{len(processed_urls) + 1}] 开始检测: {normalized_url}")
                capability = detector.analyze_page_capability(
                    page,
                    normalized_url,
                    enable_vision=bool(config.get("enable_vision", True)),
                    confidence_threshold=float(config.get("confidence_threshold", 0.8)),
                )
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                updated_row = build_source_probe_update(row, capability, timestamp)
                write_source_row_with_retry(
                    workbook,
                    source_headers,
                    int(row.get("_row_index", 0) or 0),
                    updated_row,
                    max_retries=int(config["write_retry_count"]),
                    base_sleep=float(config["write_retry_base_seconds"]),
                )
                print(
                    "   ↳ 检测完成:"
                    f" 初始={capability['static_result'].get('recommended_format', 'unknown')}"
                    f" -> 最终={capability['final_result'].get('recommended_format', 'unknown')}"
                    f" | 阶段={capability.get('stage', 'static_only')}"
                    f" | 状态={capability.get('status', 'failed')}"
                )

                result_item = {
                    "url": normalized_url,
                    "stage": capability.get("stage", "static_only"),
                    "status": capability.get("status", "failed"),
                    "vision_used": capability.get("vision_used", False),
                    "static_result": capability["static_result"],
                    "runtime_result": capability.get("runtime_result"),
                    "vision_result": capability.get("vision_result"),
                    "final_result": capability["final_result"],
                    "detected_at": timestamp,
                }
                processed_urls.add(normalized_url)
                processed_this_run.append(result_item)
                scanned += 1

                state_payload = {
                    "processed_urls": sorted(processed_urls),
                    "results": prior_results + processed_this_run,
                    "last_scanned_at": timestamp,
                    "last_url": normalized_url,
                }
                save_json(state_path, state_payload)
                if limit and scanned >= limit:
                    break
        finally:
            try:
                page.close()
            except Exception:
                pass
            try:
                browser_owner.close()
            except Exception:
                pass

    all_results = prior_results + processed_this_run
    summary = summarize_results(all_results)
    save_json(summary_path, summary)
    return {
        "processed_this_run": scanned,
        "total_processed": len(all_results),
        "summary": summary,
        "state_file": str(state_path),
        "summary_file": str(summary_path),
    }


def main():
    parser = argparse.ArgumentParser(description="批量回填来源主表的链接格式检测结果。")
    parser.add_argument("--limit", type=int, default=None, help="本次最多扫描多少条来源链接。")
    parser.add_argument("--reset-state", action="store_true", help="忽略断点状态，从头开始扫描。")
    parser.add_argument("--config", default="config.json", help="配置文件路径。")
    args = parser.parse_args()

    result = run_backfill(limit=args.limit, reset_state=args.reset_state, config_path=args.config)
    print("✅ 来源主表格式回填完成")
    print(f"   本次处理: {result['processed_this_run']}")
    print(f"   累计处理: {result['total_processed']}")
    print(f"   汇总文件: {result['summary_file']}")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
