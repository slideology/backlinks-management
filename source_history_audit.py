from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from playwright.sync_api import Page, sync_playwright

from backlink_state import TARGET_SITE_HEADERS, dynamic_source_headers
from browser_cdp import DEFAULT_CDP_URL, ensure_allowed_cdp_url, merge_browser_config
from feishu_workbook import FeishuWorkbook
from form_automation_local import (
    _deep_scroll_to_bottom,
    _diagnose_site_status,
    _find_target_presence_in_comments,
    try_dismiss_overlays,
)
from legacy_feishu_history import extract_cell_text, extract_cell_url, normalize_source_url
from source_format_backfill import (
    load_json,
    open_probe_browser,
    read_target_rows,
    save_json,
    should_retry_write,
)


DEFAULT_CONFIG = {
    "state_file": "artifacts/history_audit/state.json",
    "summary_file": "artifacts/history_audit/summary.json",
    "connect_cdp_url": DEFAULT_CDP_URL,
    "page_load_timeout_ms": 12000,
    "networkidle_timeout_ms": 5000,
    "read_page_size": 250,
    "write_retry_count": 5,
    "write_retry_base_seconds": 2,
}

GOOGLE_SSO_SELECTORS = [
    'button:has-text("Sign in with Google")',
    'button:has-text("Continue with Google")',
    'button:has-text("Login with Google")',
    'a:has-text("Sign in with Google")',
    'a:has-text("Continue with Google")',
    '.google-login-button',
    '#google-login',
    '[data-provider="google"]',
    'img[alt*="Google"][role="button"]',
    'div[data-type="google"]',
]

COMMENT_PROBE_SELECTORS = [
    'textarea:visible',
    '[contenteditable="true"]:visible',
    'input[name*="comment"]',
    '#comments',
    '.comments',
    '.comment-list',
    '.commentlist',
    '.comment-respond',
    '#respond',
]

LOGIN_HINTS = [
    "log in to comment",
    "you must be logged in",
    "sign in to comment",
    "login to comment",
    "must be logged in",
    "register to comment",
    "please sign in",
    "登录后",
    "登录以评论",
]


def load_audit_config(config_path: str = "config.json") -> dict:
    try:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_CONFIG)
    merged = {**DEFAULT_CONFIG, **payload.get("history_audit", {})}
    browser_cfg = merge_browser_config(payload.get("browser", {}) or {})
    merged["connect_cdp_url"] = str(browser_cfg.get("connect_cdp_url", "") or merged["connect_cdp_url"])
    merged["browser"] = browser_cfg
    return merged


def read_source_rows_paged(
    workbook: FeishuWorkbook,
    max_cols: int = 250,
    page_size: int = 250,
) -> list[dict]:
    deduped = {}
    selected_headers = [
        "来源链接",
        "历史审计状态",
    ]
    for row_index, row in workbook.iter_sheet_selected_rows(
        "sources",
        selected_headers=selected_headers,
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


def extract_active_target_domains(target_rows: list[dict]) -> list[tuple[str, str]]:
    items = []
    for row in target_rows:
        if str(extract_cell_text(row.get("是否启用", "")) or "").strip() != "是":
            continue
        site_key = str(extract_cell_text(row.get("站点标识", "")) or "").strip()
        target_url = normalize_source_url(extract_cell_url(row.get("目标网站", "")) or extract_cell_text(row.get("目标网站", "")))
        hostname = (urlparse(target_url).hostname or "").lower()
        if hostname.startswith("www."):
            hostname = hostname[4:]
        if hostname:
            items.append((site_key or hostname, hostname))
    return items


def _safe_visible_text(target) -> str:
    try:
        return " ".join(target.locator("body").all_inner_texts()).lower()
    except Exception:
        return ""


def detect_google_login_support(page: Page) -> tuple[str, str]:
    targets = [("page", page)] + [(f"frame:{frame.url}", frame) for frame in page.frames]
    for scope_name, target in targets:
        for selector in GOOGLE_SSO_SELECTORS:
            try:
                locator = target.locator(selector).first
                if locator.count() > 0 and locator.is_visible(timeout=300):
                    return "是", f"{scope_name}:{selector}"
            except Exception:
                continue
        text = _safe_visible_text(target)
        if any(token in text for token in ["sign in with google", "continue with google", "login with google"]):
            return "是", f"{scope_name}:text_google_sso"
    return "否", ""


def detect_comment_presence(page: Page) -> tuple[str, str]:
    targets = [("page", page)] + [(f"frame:{frame.url}", frame) for frame in page.frames]
    for scope_name, target in targets:
        for selector in COMMENT_PROBE_SELECTORS:
            try:
                locator = target.locator(selector)
                if locator.count() > 0 and locator.first.is_visible(timeout=300):
                    return "是", f"{scope_name}:{selector}"
            except Exception:
                continue
    return "否", ""


def detect_login_requirement(page: Page) -> tuple[str, str]:
    diagnosis = _diagnose_site_status(page)
    if "登录" in diagnosis or "账号" in diagnosis:
        return "是", diagnosis

    text = _safe_visible_text(page)
    if any(token in text for token in LOGIN_HINTS):
        return "是", "page_text_login_wall"

    for frame in page.frames:
        text = _safe_visible_text(frame)
        if any(token in text for token in LOGIN_HINTS):
            return "是", f"frame_text_login_wall:{frame.url}"

    return "否", ""


def detect_historical_target_presence(page: Page, target_domains: list[tuple[str, str]]) -> tuple[str, str]:
    hits = []
    targets = [("page", page)] + [(f"frame:{frame.url}", frame) for frame in page.frames]
    for site_key, hostname in target_domains:
        target_url = f"https://{hostname}/"
        for scope_name, target in targets:
            try:
                reason = _find_target_presence_in_comments(target, target_url=target_url, anchor_text="")
            except Exception:
                reason = ""
            if reason:
                hits.append(f"{site_key}@{scope_name}:{reason}")
                break

    if not hits:
        return "未发现", ""

    matched_sites = []
    for item in hits:
        site_key = item.split("@", 1)[0]
        if site_key not in matched_sites:
            matched_sites.append(site_key)
    return ",".join(matched_sites), " | ".join(hits)


def build_history_audit_update(row: dict, result: dict, now_text: str) -> dict:
    return {
        "是否需要登录": result.get("requires_login", "否"),
        "登录探测证据": result.get("login_evidence", ""),
        "是否支持Google登录": result.get("supports_google_login", "否"),
        "Google登录探测证据": result.get("google_login_evidence", ""),
        "评论区是否存在": result.get("has_comment_area", "否"),
        "评论区探测证据": result.get("comment_evidence", ""),
        "历史外链验证结果": result.get("historical_presence", "未发现"),
        "历史外链验证证据": result.get("historical_presence_evidence", ""),
        "历史审计时间": now_text,
        "历史审计状态": result.get("status", "failed"),
    }


def summarize_results(results: list[dict]) -> dict:
    status_counts = Counter()
    login_counts = Counter()
    google_counts = Counter()
    comment_counts = Counter()
    presence_counts = Counter()
    for item in results:
        status_counts[item.get("status", "failed")] += 1
        login_counts[item.get("requires_login", "否")] += 1
        google_counts[item.get("supports_google_login", "否")] += 1
        comment_counts[item.get("has_comment_area", "否")] += 1
        presence_counts[item.get("historical_presence", "未发现")] += 1

    return {
        "total_scanned": len(results),
        "status_counts": dict(status_counts),
        "requires_login_counts": dict(login_counts),
        "google_login_counts": dict(google_counts),
        "comment_area_counts": dict(comment_counts),
        "historical_presence_counts": dict(presence_counts),
    }


def audit_single_page(page: Page, url: str, target_domains: list[tuple[str, str]], config: dict) -> dict:
    page.goto(url, timeout=int(config["page_load_timeout_ms"]))
    try:
        page.wait_for_load_state("networkidle", timeout=int(config["networkidle_timeout_ms"]))
    except Exception:
        pass
    try_dismiss_overlays(page)
    _deep_scroll_to_bottom(page)

    requires_login, login_evidence = detect_login_requirement(page)
    supports_google_login, google_login_evidence = detect_google_login_support(page)
    has_comment_area, comment_evidence = detect_comment_presence(page)
    historical_presence, historical_presence_evidence = detect_historical_target_presence(page, target_domains)
    return {
        "status": "completed",
        "requires_login": requires_login,
        "login_evidence": login_evidence,
        "supports_google_login": supports_google_login,
        "google_login_evidence": google_login_evidence,
        "has_comment_area": has_comment_area,
        "comment_evidence": comment_evidence,
        "historical_presence": historical_presence,
        "historical_presence_evidence": historical_presence_evidence,
    }


def run_audit(limit: Optional[int] = None, reset_state: bool = False, config_path: str = "config.json") -> dict:
    config = load_audit_config(config_path)
    state_path = Path(config["state_file"])
    summary_path = Path(config["summary_file"])
    state = {} if reset_state else load_json(state_path, {"processed_urls": [], "results": []})
    processed_urls = set(state.get("processed_urls", []))
    prior_results = list(state.get("results", []))

    workbook = FeishuWorkbook.from_config(config_path)
    if not workbook:
        raise RuntimeError("飞书未正确配置，无法执行历史来源审计。")

    target_rows = read_target_rows(workbook)
    target_domains = extract_active_target_domains(target_rows)
    source_headers = dynamic_source_headers(target_rows)
    workbook.ensure_sheet_headers("sources", source_headers)
    print("📚 正在分页读取来源主表...")
    source_rows = read_source_rows_paged(
        workbook,
        max_cols=max(len(source_headers), 250),
        page_size=int(config.get("read_page_size", 250)),
    )
    print(f"📦 已加载待审计来源 {len(source_rows)} 条（去重后）")

    processed_this_run = []
    scanned = 0

    with sync_playwright() as p:
        cdp_url = ensure_allowed_cdp_url(config["connect_cdp_url"], config.get("browser", {}))
        print(f"🌐 历史审计仅连接 CDP: {cdp_url}")
        browser_owner, _, page, _ = open_probe_browser(p, cdp_url)
        try:
            page.set_default_timeout(int(config["page_load_timeout_ms"]))
            for row in source_rows:
                normalized_url = normalize_source_url(
                    extract_cell_url(row.get("来源链接", "")) or extract_cell_text(row.get("来源链接", ""))
                )
                if not normalized_url or normalized_url in processed_urls:
                    continue

                print(f"🔍 [{len(processed_urls) + 1}] 开始审计: {normalized_url}")
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                try:
                    audit_result = audit_single_page(page, normalized_url, target_domains, config)
                except Exception as exc:
                    audit_result = {
                        "status": "failed",
                        "requires_login": "",
                        "login_evidence": "",
                        "supports_google_login": "",
                        "google_login_evidence": "",
                        "has_comment_area": "",
                        "comment_evidence": "",
                        "historical_presence": "",
                        "historical_presence_evidence": str(exc)[:200],
                    }

                updated_row = build_history_audit_update(row, audit_result, timestamp)
                write_source_row_with_retry(
                    workbook,
                    source_headers,
                    int(row.get("_row_index", 0) or 0),
                    updated_row,
                    max_retries=int(config["write_retry_count"]),
                    base_sleep=float(config["write_retry_base_seconds"]),
                )
                print(
                    "   ↳ 审计完成:"
                    f" 登录={audit_result.get('requires_login', '') or '未知'}"
                    f" | Google={audit_result.get('supports_google_login', '') or '未知'}"
                    f" | 评论区={audit_result.get('has_comment_area', '') or '未知'}"
                    f" | 历史命中={audit_result.get('historical_presence', '') or '未知'}"
                    f" | 状态={audit_result.get('status', 'failed')}"
                )

                result_item = {
                    "url": normalized_url,
                    "detected_at": timestamp,
                    **audit_result,
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
    parser = argparse.ArgumentParser(description="批量审计来源主表的登录/评论能力与历史外链存在性。")
    parser.add_argument("--limit", type=int, default=None, help="本次最多扫描多少条来源链接。")
    parser.add_argument("--reset-state", action="store_true", help="忽略断点状态，从头开始扫描。")
    parser.add_argument("--config", default="config.json", help="配置文件路径。")
    args = parser.parse_args()

    result = run_audit(limit=args.limit, reset_state=args.reset_state, config_path=args.config)
    print("✅ 来源主表历史审计完成")
    print(f"   本次处理: {result['processed_this_run']}")
    print(f"   累计处理: {result['total_processed']}")
    print(f"   汇总文件: {result['summary_file']}")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
