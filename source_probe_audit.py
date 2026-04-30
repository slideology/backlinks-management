from __future__ import annotations

import argparse
import csv
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

from browser_cdp import ensure_allowed_cdp_url, merge_browser_config, normalize_cdp_url
from backlink_state import (
    EXECUTION_MODE_CLASSIC,
    PROBE_STATUS_COMPLETED,
    PROBE_STATUS_FAILED,
    PROBE_STATUS_PENDING,
    PROBE_STATUS_SKIPPED,
    PROBE_WORTH_NO,
    PROBE_WORTH_REVIEW,
    PROBE_WORTH_YES,
    TARGET_SITE_HEADERS,
    dynamic_source_headers,
)
from feishu_workbook import FeishuWorkbook
from form_automation_local import _preprobe_page_for_generation
from legacy_feishu_history import extract_cell_text, extract_cell_url, normalize_source_url
from source_format_backfill import (
    build_source_probe_update,
    load_json,
    open_probe_browser,
    read_target_rows,
    save_json,
    should_retry_write,
)
from source_history_audit import (
    audit_single_page,
    build_history_audit_update,
    extract_active_target_domains,
)
from sync_reporting_workbook import _normalize_excluded_domains, _normalize_excluded_urls
from website_format_detector import WebsiteFormatDetector


DEFAULT_CONFIG = {
    "state_file": "artifacts/page_probe/state.json",
    "summary_file": "artifacts/page_probe/summary.json",
    "report_json_file": "artifacts/page_probe/source_probe_results.json",
    "report_csv_file": "artifacts/page_probe/source_probe_results.csv",
    "reclassified_to_yes_json_file": "artifacts/page_probe/reclassified_to_yes.json",
    "reclassified_to_yes_csv_file": "artifacts/page_probe/reclassified_to_yes.csv",
    "reason_corrected_only_json_file": "artifacts/page_probe/reason_corrected_only.json",
    "reason_corrected_only_csv_file": "artifacts/page_probe/reason_corrected_only.csv",
    "still_review_after_reprobe_json_file": "artifacts/page_probe/still_review_after_reprobe.json",
    "still_review_after_reprobe_csv_file": "artifacts/page_probe/still_review_after_reprobe.csv",
    "connect_cdp_url": "http://127.0.0.1:9667",
    "allow_only_cdp_url": "http://127.0.0.1:9667",
    "page_load_timeout_ms": 15000,
    "single_page_timeout_seconds": 90,
    "light_probe_timeout_seconds": 25,
    "heavy_probe_timeout_seconds": 60,
    "read_page_size": 250,
    "write_retry_count": 5,
    "write_retry_base_seconds": 2,
    "confidence_threshold": 0.8,
    "enable_vision": True,
    "isolated_probe_worker": True,
    "worker_poll_interval_seconds": 10,
    "worker_timeout_buffer_seconds": 20,
    "worker_retry_on_browser_restart": 1,
    "fresh_browser_per_run": True,
    "profile_dir": str(Path.home() / "ChromeCanaryProbe9667"),
    "disable_extensions": True,
    "launch_in_background": True,
    "hide_after_launch": True,
}


class ProbePageTimeoutError(TimeoutError):
    pass


@contextmanager
def probe_page_timeout_guard(seconds: int):
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _handle_timeout(_signum, _frame):
        raise ProbePageTimeoutError(f"页面探测超时（>{seconds}秒）")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def load_probe_task_config(config_path: str = "config.json") -> dict:
    try:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    merged = {**DEFAULT_CONFIG, **payload.get("page_probe", {})}
    browser_base = payload.get("browser", {}) or {}
    browser_override = {
        key: merged.get(key)
        for key in (
            "connect_cdp_url",
            "allow_only_cdp_url",
            "profile_dir",
            "disable_extensions",
            "launch_in_background",
            "hide_after_launch",
        )
    }
    browser_cfg = merge_browser_config({**browser_base, **browser_override})
    browser_cfg["profile_dir"] = str(browser_cfg.get("profile_dir") or merged["profile_dir"])
    browser_cfg["disable_extensions"] = bool(browser_cfg.get("disable_extensions", True))
    browser_cfg["launch_in_background"] = bool(browser_cfg.get("launch_in_background", True))
    browser_cfg["hide_after_launch"] = bool(browser_cfg.get("hide_after_launch", True))
    merged["browser"] = browser_cfg
    merged["connect_cdp_url"] = str(browser_cfg.get("connect_cdp_url", "") or merged["connect_cdp_url"])
    merged["allow_only_cdp_url"] = str(browser_cfg.get("allow_only_cdp_url", "") or merged["allow_only_cdp_url"])
    merged["excluded_source_domains"] = list((payload.get("reporting_workbook", {}) or {}).get("excluded_source_domains", []) or [])
    merged["excluded_source_urls"] = list((payload.get("reporting_workbook", {}) or {}).get("excluded_source_urls", []) or [])
    return merged


def _probe_browser_app_path() -> str:
    canary = Path("/Applications/Google Chrome Canary.app")
    stable = Path("/Applications/Google Chrome.app")
    if canary.exists():
        return str(canary)
    return str(stable)


def _cdp_http_ready(cdp_url: str, timeout_seconds: int = 2) -> bool:
    normalized = normalize_cdp_url(cdp_url)
    parsed = urlparse(normalized)
    base = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    try:
        with urlopen(f"{base}/json/version", timeout=timeout_seconds) as resp:
            return resp.status == 200
    except Exception:
        return False


def _ensure_probe_browser_target_isolated(config: dict) -> str:
    probe_cdp = normalize_cdp_url(config["connect_cdp_url"])
    probe_port = urlparse(probe_cdp).port or 9667
    if probe_port == 9666:
        raise RuntimeError("批量探测禁止复用 9666 端口，请改用独立探测端口。")
    browser_cfg = config.get("browser", {}) or {}
    allowed = normalize_cdp_url(str(browser_cfg.get("allow_only_cdp_url", "") or probe_cdp))
    if normalize_cdp_url(allowed) != probe_cdp:
        raise RuntimeError(f"批量探测的 CDP 白名单必须与探测端口一致：{probe_cdp}")
    return probe_cdp


def ensure_probe_browser_ready(config: dict) -> str:
    probe_cdp = _ensure_probe_browser_target_isolated(config)
    fresh_browser = bool(config.get("fresh_browser_per_run", True))
    if _cdp_http_ready(probe_cdp) and not fresh_browser:
        return probe_cdp

    browser_cfg = config.get("browser", {}) or {}
    profile_dir = str(browser_cfg.get("profile_dir") or DEFAULT_CONFIG["profile_dir"])
    port = urlparse(probe_cdp).port or 9667
    Path(profile_dir).mkdir(parents=True, exist_ok=True)
    subprocess.run(["pkill", "-f", profile_dir], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1)

    chrome_args = [
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-default-apps",
        "--disable-background-networking",
    ]
    if browser_cfg.get("disable_extensions", True):
        chrome_args.extend(["--disable-extensions", "--disable-component-extensions-with-background-pages"])

    open_cmd = ["open"]
    if browser_cfg.get("launch_in_background", True):
        open_cmd.append("-g")
    open_cmd.extend(["-na", _probe_browser_app_path(), "--args", *chrome_args])
    subprocess.run(open_cmd, check=True)

    deadline = time.time() + 20
    while time.time() < deadline:
        if _cdp_http_ready(probe_cdp):
            return probe_cdp
        time.sleep(1)
    raise RuntimeError(f"批量探测浏览器未能在预期时间内启动：{probe_cdp}")


def read_source_rows_paged(
    workbook: FeishuWorkbook,
    max_cols: int = 250,
    page_size: int = 250,
) -> list[dict]:
    deduped = {}
    selected_headers = [
        "来源链接",
        "页面探测状态",
        "页面探测时间",
        "是否值得发帖",
        "页面探测失败原因",
        "推荐策略",
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


def _timeout_like_reason(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in ("超时", "timeout", "probe_timeout", "goto_timeout"))


def _login_like_reason(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in ("登录", "login", "log in", "sign in", "register", "page_text_login_wall"))


def _challenge_like_reason(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in ("cloudflare", "captcha", "challenge", "验证码"))


def _comment_closed_like_reason(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    return any(marker in normalized for marker in ("评论已关闭", "comments closed", "comment closed"))


def _promotable_review_reason(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return True
    return any(
        marker in normalized
        for marker in (
            "格式检测存在冲突",
            "unknown",
            "runtime_unknown",
            "runtime unknown",
            "冲突",
            "超时",
            "timeout",
            "probe_timeout",
            "goto_timeout",
            "页面探测子进程超时",
            "页面探测超时",
            "格式探测失败",
        )
    )


def _browser_session_broken(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    return any(
        marker in normalized
        for marker in (
            "target page, context or browser has been closed",
            "connection closed while reading from the driver",
            "browsertype.connect_over_cdp",
            "service_worker",
        )
    )


def _probe_failed_capability(reason: str) -> dict:
    evidence_type = "probe_timeout" if _timeout_like_reason(reason) else "probe_failed"
    return {
        "static_result": {
            "recommended_format": "unknown",
            "evidence_type": evidence_type,
            "confidence": 0,
        },
        "final_result": {
            "recommended_format": "unknown",
            "evidence_type": evidence_type,
            "confidence": 0,
        },
        "stage": "static_only",
        "vision_used": False,
        "status": "failed",
        "runtime_result": None,
        "vision_result": {"reason": reason},
    }


def _skipped_capability_from_row(row: dict, reason: str) -> dict:
    initial_format = str(extract_cell_text(row.get("初始链接格式", "")) or "").strip() or "unknown"
    final_format = str(extract_cell_text(row.get("最终链接格式", "")) or "").strip() or initial_format
    try:
        confidence = float(extract_cell_text(row.get("格式检测置信度", "")) or 0)
    except Exception:
        confidence = 0.0
    stage = str(extract_cell_text(row.get("格式检测阶段", "")) or "").strip() or "light_probe_only"
    evidence = str(extract_cell_text(row.get("格式检测证据", "")) or "").strip() or "light_probe_only"
    return {
        "static_result": {
            "recommended_format": initial_format,
            "evidence_type": evidence,
            "confidence": confidence,
        },
        "final_result": {
            "recommended_format": final_format,
            "evidence_type": reason or evidence,
            "confidence": confidence,
        },
        "stage": stage,
        "vision_used": False,
        "status": "skipped",
        "runtime_result": None,
        "vision_result": None,
    }


def build_probe_timeout_bundle(reason: str) -> tuple[dict, dict, dict]:
    message = reason or "页面探测超时"
    audit_result = {
        "status": "failed",
        "requires_login": "",
        "login_evidence": "",
        "supports_google_login": "",
        "google_login_evidence": "",
        "has_comment_area": "",
        "comment_evidence": "",
        "historical_presence": "",
        "historical_presence_evidence": message,
    }
    capability = _probe_failed_capability(message)
    preprobe_meta = {
        "ok": False,
        "message": "页面预探测超时",
        "diagnosis": message,
        "diagnostic_category": "preprobe_timeout",
        "recommended_strategy": "dom",
    }
    return audit_result, capability, preprobe_meta


def _light_probe_bool(meta: dict, key: str) -> bool:
    return bool(meta.get(key, False))


def _light_probe_reason(meta: dict) -> str:
    return str(meta.get("diagnosis", "") or meta.get("message", "")).strip()


def _has_strong_positive_signals(audit_result: dict, preprobe_meta: dict) -> bool:
    if str(audit_result.get("requires_login", "") or "") == "是":
        return False
    if _light_probe_bool(preprobe_meta, "login_wall_after_expand"):
        return False
    if _light_probe_bool(preprobe_meta, "challenge_after_expand"):
        return False
    if _light_probe_bool(preprobe_meta, "comment_closed_after_expand"):
        return False
    if _light_probe_bool(preprobe_meta, "comment_form_visible"):
        return True
    if _light_probe_bool(preprobe_meta, "comment_entry_expanded"):
        return True
    if _light_probe_bool(preprobe_meta, "comment_entry_found") and str(audit_result.get("has_comment_area", "") or "") == "是":
        return True
    return bool(preprobe_meta.get("ok", False) and _light_probe_bool(preprobe_meta, "comment_signal_found"))


def _build_probe_classification(status: str, worth: str, reason: str) -> dict:
    return {
        "页面探测状态": status,
        "是否值得发帖": worth,
        "页面探测失败原因": reason,
    }


def assess_light_probe(
    url: str,
    audit_result: dict,
    preprobe_meta: dict,
    excluded_domains: set[str],
    excluded_urls: set[str],
) -> dict:
    if _url_matches_exclusions(url, excluded_domains, excluded_urls):
        return {
            "run_heavy_probe": False,
            "classification": _build_probe_classification(PROBE_STATUS_SKIPPED, PROBE_WORTH_NO, "命中现有来源排除规则"),
        }

    audit_completed = str(audit_result.get("status", "") or "") == "completed"
    requires_login = str(audit_result.get("requires_login", "") or "") == "是"
    has_comment_area = str(audit_result.get("has_comment_area", "") or "") == "是"
    category = str(preprobe_meta.get("diagnostic_category", "") or "").strip()
    diagnosis = _light_probe_reason(preprobe_meta)
    strong_positive = _has_strong_positive_signals(audit_result, preprobe_meta)

    if category == "challenge":
        return {
            "run_heavy_probe": False,
            "classification": _build_probe_classification(
                PROBE_STATUS_COMPLETED,
                PROBE_WORTH_NO,
                diagnosis or "页面存在验证码或 Cloudflare 挑战",
            ),
        }

    if category == "comments_closed":
        return {
            "run_heavy_probe": False,
            "classification": _build_probe_classification(
                PROBE_STATUS_COMPLETED,
                PROBE_WORTH_NO,
                diagnosis or "页面提示评论已关闭",
            ),
        }

    if audit_completed and requires_login:
        return {
            "run_heavy_probe": False,
            "classification": _build_probe_classification(
                PROBE_STATUS_COMPLETED,
                PROBE_WORTH_NO,
                str(audit_result.get("login_evidence", "") or diagnosis or "必须登录后才能评论"),
            ),
        }

    if category == "login_required":
        return {
            "run_heavy_probe": False,
            "classification": _build_probe_classification(
                PROBE_STATUS_COMPLETED,
                PROBE_WORTH_NO,
                diagnosis or "必须登录后才能评论",
            ),
        }

    if not audit_completed and not strong_positive:
        return {
            "run_heavy_probe": False,
            "classification": _build_probe_classification(
                PROBE_STATUS_FAILED,
                PROBE_WORTH_REVIEW,
                str(audit_result.get("historical_presence_evidence", "") or diagnosis or "历史审计失败"),
            ),
        }

    if not preprobe_meta.get("ok", True):
        if category == "comment_signal_missing" and has_comment_area:
            return {"run_heavy_probe": True, "classification": None}
        if category == "comment_signal_missing" and not has_comment_area and not strong_positive:
            return {
                "run_heavy_probe": False,
                "classification": _build_probe_classification(
                    PROBE_STATUS_COMPLETED,
                    PROBE_WORTH_NO,
                    str(audit_result.get("comment_evidence", "") or diagnosis or "未发现评论区"),
                ),
            }
        return {
            "run_heavy_probe": False,
            "classification": _build_probe_classification(
                PROBE_STATUS_FAILED,
                PROBE_WORTH_REVIEW,
                diagnosis or "页面预探测失败",
            ),
        }

    if strong_positive:
        return {"run_heavy_probe": True, "classification": None}

    if has_comment_area:
        return {"run_heavy_probe": True, "classification": None}

    return {
        "run_heavy_probe": False,
        "classification": _build_probe_classification(
            PROBE_STATUS_COMPLETED,
            PROBE_WORTH_NO,
            str(audit_result.get("comment_evidence", "") or "未发现评论区"),
        ),
    }


def _is_promotable_capability_result(capability: dict) -> bool:
    status = str(capability.get("status", "") or "").strip()
    if status == "conflict":
        return True
    if status == "completed":
        return True
    final_result = capability.get("final_result", {}) or {}
    evidence = str(final_result.get("evidence_type", "") or "").strip().lower()
    return evidence in {"unknown", "runtime_unknown", "probe_timeout", "probe_failed"}


def _existing_probe_flag(existing_probe: dict | None, key: str, yes_text: bool = False) -> bool:
    existing_probe = existing_probe or {}
    value = existing_probe.get(key, False)
    if isinstance(value, bool):
        return value
    text = str(value or "").strip()
    if yes_text:
        return text == "是"
    return text.lower() in {"true", "1", "yes"}


def _existing_probe_text(existing_probe: dict | None, key: str) -> str:
    existing_probe = existing_probe or {}
    return str(existing_probe.get(key, "") or "").strip()


def _should_inherit_existing_probe(preprobe_meta: dict, capability: dict) -> bool:
    reason_text = " ".join(
        part
        for part in (
            _light_probe_reason(preprobe_meta),
            str((capability.get("final_result", {}) or {}).get("evidence_type", "") or "").strip(),
            str((capability.get("vision_result", {}) or {}).get("reason", "") or "").strip(),
        )
        if part
    )
    category = str(preprobe_meta.get("diagnostic_category", "") or "").strip().lower()
    return _timeout_like_reason(reason_text) or category in {"preprobe_timeout", "goto_timeout"}


def _classification_from_existing_probe(existing_probe: dict | None) -> dict | None:
    if not existing_probe:
        return None

    existing_reason = _existing_probe_text(existing_probe, "页面探测失败原因")
    requires_login = _existing_probe_flag(existing_probe, "是否需要登录", yes_text=True) or _existing_probe_flag(
        existing_probe, "login_wall_after_expand"
    )
    challenge = _existing_probe_flag(existing_probe, "challenge_after_expand") or _challenge_like_reason(existing_reason)
    comment_closed = _existing_probe_flag(existing_probe, "comment_closed_after_expand") or _comment_closed_like_reason(
        existing_reason
    )
    has_comment_area = _existing_probe_flag(existing_probe, "评论区是否存在", yes_text=True)
    strong_comment_path = any(
        (
            _existing_probe_flag(existing_probe, "comment_form_visible"),
            _existing_probe_flag(existing_probe, "comment_entry_expanded"),
            _existing_probe_flag(existing_probe, "comment_entry_found"),
        )
    )
    previous_worth = _existing_probe_text(existing_probe, "是否值得发帖")

    if requires_login or _login_like_reason(existing_reason):
        return _build_probe_classification(
            PROBE_STATUS_COMPLETED,
            PROBE_WORTH_NO,
            existing_reason or "必须登录后才能评论",
        )

    if challenge:
        return _build_probe_classification(
            PROBE_STATUS_COMPLETED,
            PROBE_WORTH_NO,
            existing_reason or "页面存在验证码或 Cloudflare 挑战",
        )

    if comment_closed:
        return _build_probe_classification(
            PROBE_STATUS_COMPLETED,
            PROBE_WORTH_NO,
            existing_reason or "页面提示评论已关闭",
        )

    if has_comment_area:
        if previous_worth == PROBE_WORTH_YES:
            return _build_probe_classification(PROBE_STATUS_COMPLETED, PROBE_WORTH_YES, "")
        if previous_worth == PROBE_WORTH_REVIEW and (strong_comment_path or _promotable_review_reason(existing_reason)):
            return _build_probe_classification(PROBE_STATUS_COMPLETED, PROBE_WORTH_YES, "")

    return None


def _build_existing_probe_snapshot(row: dict, previous_result: dict | None = None) -> dict:
    snapshot = {
        "来源链接": normalize_source_url(
            extract_cell_url(row.get("来源链接", "")) or extract_cell_text(row.get("来源链接", ""))
        )
        or "",
        "页面探测状态": str(extract_cell_text(row.get("页面探测状态", "")) or "").strip(),
        "是否值得发帖": str(extract_cell_text(row.get("是否值得发帖", "")) or "").strip(),
        "页面探测失败原因": str(extract_cell_text(row.get("页面探测失败原因", "")) or "").strip(),
        "评论区是否存在": str(extract_cell_text(row.get("评论区是否存在", "")) or "").strip(),
        "是否需要登录": str(extract_cell_text(row.get("是否需要登录", "")) or "").strip(),
        "推荐策略": str(extract_cell_text(row.get("推荐策略", "")) or "").strip(),
    }
    if previous_result:
        for key in (
            "页面探测状态",
            "是否值得发帖",
            "页面探测失败原因",
            "评论区是否存在",
            "是否需要登录",
            "推荐策略",
            "comment_entry_found",
            "comment_entry_expanded",
            "comment_form_visible",
            "login_wall_after_expand",
            "challenge_after_expand",
            "comment_closed_after_expand",
        ):
            if key in previous_result and previous_result.get(key, "") not in ("", None):
                snapshot[key] = previous_result[key]
    return snapshot


def _audit_result_from_preprobe_meta(preprobe_meta: dict, fallback_status: str = "completed") -> dict:
    diagnosis = _light_probe_reason(preprobe_meta)
    category = str(preprobe_meta.get("diagnostic_category", "") or "").strip()
    has_comment_area = any(
        (
            bool(preprobe_meta.get("comment_signal_found", False)),
            bool(preprobe_meta.get("comment_entry_found", False)),
            bool(preprobe_meta.get("comment_entry_expanded", False)),
            bool(preprobe_meta.get("comment_form_visible", False)),
            category in {"login_required", "challenge", "comments_closed"},
        )
    )
    requires_login = category == "login_required" or bool(preprobe_meta.get("login_wall_after_expand", False))
    return {
        "status": fallback_status if has_comment_area or requires_login else "failed",
        "requires_login": "是" if requires_login else "否",
        "login_evidence": diagnosis if requires_login else "",
        "supports_google_login": "",
        "google_login_evidence": "",
        "has_comment_area": "是" if has_comment_area else "否",
        "comment_evidence": diagnosis if has_comment_area else "",
        "historical_presence": "",
        "historical_presence_evidence": diagnosis if not has_comment_area else "",
    }


def _current_python_bin() -> str:
    return sys.executable


def _url_matches_exclusions(url: str, excluded_domains: set[str], excluded_urls: set[str]) -> bool:
    normalized = normalize_source_url(url)
    if not normalized:
        return False
    if normalized in excluded_urls:
        return True
    hostname = (urlparse(normalized).hostname or "").strip().lower()
    if not hostname:
        return False
    return any(hostname == domain or hostname.endswith(f".{domain}") for domain in excluded_domains)


def _choose_recommended_strategy(existing_strategy: str, preprobe_meta: dict, capability: dict) -> str:
    preprobe_strategy = str(preprobe_meta.get("recommended_strategy", "") or "").strip().lower()
    if preprobe_strategy in {"dom", "iframe", "vision", "skip"}:
        return preprobe_strategy

    evidence = str(capability.get("final_result", {}).get("evidence_type", "") or "").lower()
    if any(marker in evidence for marker in ("iframe", "blogger", "disqus", "comment_frame")):
        return "iframe"

    current = str(existing_strategy or "").strip().lower()
    if current in {"dom", "iframe", "vision", "skip"}:
        return current
    return "dom"


def _cleanup_probe_context_pages(context, primary_page, expected_url: str) -> None:
    expected_host = (urlparse(expected_url).hostname or "").strip().lower()
    for candidate in list(getattr(context, "pages", []) or []):
        if candidate == primary_page:
            continue
        try:
            candidate_url = str(getattr(candidate, "url", "") or "").strip()
            candidate_host = (urlparse(candidate_url).hostname or "").strip().lower()
            if not candidate_url or candidate_url == "about:blank":
                candidate.close()
                continue
            # The probe only needs one top-level page; close cross-site popups/tabs
            # such as social shares or consent helpers that get opened accidentally.
            if candidate_host and candidate_host != expected_host:
                candidate.close()
        except Exception:
            continue


def _build_probe_worker_payload(
    row: dict,
    normalized_url: str,
    existing_probe: dict,
    config: dict,
    excluded_domains: set[str],
    excluded_urls: set[str],
    target_domains: list[str],
) -> dict:
    return {
        "row": row,
        "normalized_url": normalized_url,
        "existing_probe": existing_probe,
        "config": config,
        "excluded_domains": sorted(excluded_domains),
        "excluded_urls": sorted(excluded_urls),
        "target_domains": list(target_domains),
    }


def _run_probe_worker(task_payload: dict) -> dict:
    config = dict(task_payload["config"])
    row = dict(task_payload["row"])
    normalized_url = str(task_payload["normalized_url"])
    existing_probe = dict(task_payload.get("existing_probe", {}) or {})
    excluded_domains = set(task_payload.get("excluded_domains", []))
    excluded_urls = set(task_payload.get("excluded_urls", []))
    target_domains = list(task_payload.get("target_domains", []))

    detector = WebsiteFormatDetector()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    light_timeout_seconds = int(config.get("light_probe_timeout_seconds", 25) or 25)
    heavy_timeout_seconds = int(config.get("heavy_probe_timeout_seconds", 60) or 60)

    with sync_playwright() as p:
        cdp_url = ensure_allowed_cdp_url(config["connect_cdp_url"], config.get("browser", {}))
        browser_owner, context, page, _ = open_probe_browser(p, cdp_url)
        try:
            page.set_default_timeout(int(config["page_load_timeout_ms"]))
            _cleanup_probe_context_pages(context, page, normalized_url)

            if _url_matches_exclusions(normalized_url, excluded_domains, excluded_urls):
                audit_result = {
                    "status": "completed",
                    "requires_login": "否",
                    "login_evidence": "",
                    "supports_google_login": "否",
                    "google_login_evidence": "",
                    "has_comment_area": "否",
                    "comment_evidence": "",
                    "historical_presence": "未发现",
                    "historical_presence_evidence": "",
                }
                capability = _probe_failed_capability("命中现有来源排除规则")
                preprobe_meta = {
                    "ok": False,
                    "message": "命中现有来源排除规则",
                    "diagnosis": "命中现有来源排除规则",
                    "diagnostic_category": "hard_blocker",
                    "recommended_strategy": "skip",
                }
                capability = _skipped_capability_from_row(row, "命中现有来源排除规则")
            else:
                preprobe_meta = {
                    "ok": False,
                    "message": "页面预探测未开始",
                    "diagnosis": "",
                    "diagnostic_category": "",
                    "recommended_strategy": str(extract_cell_text(row.get("推荐策略", "")) or "dom"),
                }
                try:
                    with probe_page_timeout_guard(light_timeout_seconds):
                        try:
                            preprobe_meta = _preprobe_page_for_generation(
                                page,
                                normalized_url,
                                int(config["page_load_timeout_ms"]),
                                EXECUTION_MODE_CLASSIC,
                                str(extract_cell_text(row.get("推荐策略", "")) or "dom"),
                            )
                        except Exception as exc:
                            preprobe_meta = {
                                "ok": False,
                                "message": "页面预探测失败",
                                "diagnosis": str(exc)[:200],
                                "diagnostic_category": "preprobe_failed",
                                "recommended_strategy": str(extract_cell_text(row.get("推荐策略", "")) or "dom"),
                            }
                        preprobe_category = str(preprobe_meta.get("diagnostic_category", "") or "").strip()
                        if preprobe_category in {"login_required", "challenge", "comments_closed"}:
                            audit_result = _audit_result_from_preprobe_meta(preprobe_meta)
                        else:
                            audit_result = audit_single_page(page, normalized_url, target_domains, config)

                    light_decision = assess_light_probe(
                        normalized_url,
                        audit_result,
                        preprobe_meta,
                        excluded_domains,
                        excluded_urls,
                    )

                    if light_decision["run_heavy_probe"]:
                        try:
                            with probe_page_timeout_guard(heavy_timeout_seconds):
                                capability = detector.analyze_page_capability(
                                    page,
                                    normalized_url,
                                    enable_vision=bool(config.get("enable_vision", True)),
                                    confidence_threshold=float(config.get("confidence_threshold", 0.8)),
                                )
                        except ProbePageTimeoutError:
                            capability = _probe_failed_capability("页面探测子进程超时（>140s）")
                        except Exception as exc:
                            if _browser_session_broken(str(exc)):
                                raise
                            capability = _probe_failed_capability(str(exc)[:200])
                    else:
                        capability = _skipped_capability_from_row(
                            row,
                            light_decision["classification"]["页面探测失败原因"],
                        )
                except ProbePageTimeoutError as exc:
                    if _has_strong_positive_signals(_audit_result_from_preprobe_meta(preprobe_meta), preprobe_meta) or str(
                        preprobe_meta.get("diagnostic_category", "") or ""
                    ).strip() in {"login_required", "challenge", "comments_closed"}:
                        audit_result = _audit_result_from_preprobe_meta(preprobe_meta)
                        capability = _probe_failed_capability(str(exc))
                    else:
                        audit_result, capability, preprobe_meta = build_probe_timeout_bundle(str(exc))
                except Exception as exc:
                    if _browser_session_broken(str(exc)):
                        raise
                    if _has_strong_positive_signals(_audit_result_from_preprobe_meta(preprobe_meta), preprobe_meta) or str(
                        preprobe_meta.get("diagnostic_category", "") or ""
                    ).strip() in {"login_required", "challenge", "comments_closed"}:
                        audit_result = _audit_result_from_preprobe_meta(preprobe_meta)
                        capability = _probe_failed_capability(str(exc)[:200])
                    else:
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
                        capability = _probe_failed_capability(str(exc)[:200])
                        preprobe_meta = {
                            "ok": False,
                            "message": "页面预探测失败",
                            "diagnosis": str(exc)[:200],
                            "diagnostic_category": "preprobe_failed",
                            "recommended_strategy": str(extract_cell_text(row.get("推荐策略", "")) or "dom"),
                        }
                        capability = _probe_failed_capability(str(exc)[:200])

            classification = classify_probe_outcome(
                normalized_url,
                audit_result,
                capability,
                preprobe_meta,
                excluded_domains,
                excluded_urls,
                existing_probe=existing_probe,
            )
            recommended_strategy = _choose_recommended_strategy(
                str(extract_cell_text(row.get("推荐策略", "")) or ""),
                preprobe_meta,
                capability,
            )
            updates = {}
            updates.update(build_history_audit_update(row, audit_result, timestamp))
            updates.update(build_source_probe_update(row, capability, timestamp))
            updates.update(build_page_probe_update(row, classification, timestamp, recommended_strategy))
            result_item = {
                "来源链接": normalized_url,
                "页面探测状态": classification["页面探测状态"],
                "页面探测时间": timestamp,
                "是否值得发帖": classification["是否值得发帖"],
                "评论区是否存在": audit_result.get("has_comment_area", ""),
                "是否需要登录": audit_result.get("requires_login", ""),
                "是否支持Google登录": audit_result.get("supports_google_login", ""),
                "最终链接格式": capability.get("final_result", {}).get("recommended_format", "unknown"),
                "推荐策略": recommended_strategy,
                "页面探测失败原因": classification["页面探测失败原因"],
                "comment_entry_found": bool(preprobe_meta.get("comment_entry_found", False)),
                "comment_entry_expanded": bool(preprobe_meta.get("comment_entry_expanded", False)),
                "comment_form_visible": bool(preprobe_meta.get("comment_form_visible", False)),
                "login_wall_after_expand": bool(preprobe_meta.get("login_wall_after_expand", False)),
                "challenge_after_expand": bool(preprobe_meta.get("challenge_after_expand", False)),
                "comment_closed_after_expand": bool(preprobe_meta.get("comment_closed_after_expand", False)),
            }
            _cleanup_probe_context_pages(context, page, normalized_url)
            return {
                "ok": True,
                "normalized_url": normalized_url,
                "timestamp": timestamp,
                "updates": updates,
                "result_item": result_item,
            }
        finally:
            try:
                if not page.is_closed():
                    page.close()
            except Exception:
                pass
            # Do not close the shared probe browser here. Each worker runs in its
            # own process; exiting the process is enough to drop the CDP session,
            # while closing the browser object can tear down the shared 9667 browser
            # and make the next worker reconnect fail.


def _run_probe_row_via_subprocess(task_payload: dict, config: dict) -> dict:
    timeout_seconds = int(config.get("single_page_timeout_seconds", 0) or 0)
    poll_interval = max(2, int(config.get("worker_poll_interval_seconds", 10) or 10))
    timeout_buffer = max(5, int(config.get("worker_timeout_buffer_seconds", 20) or 20))
    retry_on_browser_restart = max(0, int(config.get("worker_retry_on_browser_restart", 1) or 0))

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as task_fp:
        json.dump(task_payload, task_fp, ensure_ascii=False)
        task_path = task_fp.name

    proc = None
    result_path = ""
    normalized_url = str(task_payload["normalized_url"])
    try:
        for attempt in range(retry_on_browser_restart + 1):
            ensure_probe_browser_ready({**config, "fresh_browser_per_run": attempt > 0})
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as result_fp:
                result_path = result_fp.name

            proc = subprocess.Popen(
                [
                    _current_python_bin(),
                    str(Path(__file__).resolve()),
                    "--worker-task-file",
                    task_path,
                    "--worker-result-file",
                    result_path,
                ],
                cwd=str(Path(__file__).resolve().parent),
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
            started_at = time.time()
            next_heartbeat = poll_interval
            hard_timeout = timeout_seconds + timeout_buffer if timeout_seconds > 0 else 0

            while True:
                rc = proc.poll()
                elapsed = int(time.time() - started_at)
                if rc is not None:
                    break
                if elapsed >= next_heartbeat:
                    print(f"  ⏳ 单页面探测子进程仍在执行（{elapsed}s）...")
                    next_heartbeat += poll_interval
                if hard_timeout > 0 and elapsed >= hard_timeout:
                    print(f"  ⏱️ 单页面探测子进程超时（>{hard_timeout}s），正在终止...")
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                    break
                time.sleep(1)

            if Path(result_path).exists():
                try:
                    payload = json.loads(Path(result_path).read_text(encoding="utf-8") or "{}")
                    if isinstance(payload, dict) and payload:
                        return payload
                except Exception:
                    pass

            if rc not in (None, 0) and attempt < retry_on_browser_restart:
                print("  ♻️ 单页面探测 worker 异常退出，重启 9667 探测浏览器后重试一次...")
                ensure_probe_browser_ready({**config, "fresh_browser_per_run": True})
                try:
                    Path(result_path).unlink(missing_ok=True)
                except Exception:
                    pass
                continue
            try:
                Path(result_path).unlink(missing_ok=True)
            except Exception:
                pass
            break

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        timeout_reason = f"页面探测子进程超时（>{hard_timeout}s）" if hard_timeout > 0 else "页面探测子进程异常退出"
        row = dict(task_payload["row"])
        audit_result, capability, preprobe_meta = build_probe_timeout_bundle(timeout_reason)
        classification = classify_probe_outcome(
            normalized_url,
            audit_result,
            capability,
            preprobe_meta,
            set(task_payload.get("excluded_domains", [])),
            set(task_payload.get("excluded_urls", [])),
            existing_probe=dict(task_payload.get("existing_probe", {}) or {}),
        )
        recommended_strategy = _choose_recommended_strategy(
            str(extract_cell_text(row.get("推荐策略", "")) or ""),
            preprobe_meta,
            capability,
        )
        updates = {}
        updates.update(build_history_audit_update(row, audit_result, timestamp))
        updates.update(build_source_probe_update(row, capability, timestamp))
        updates.update(build_page_probe_update(row, classification, timestamp, recommended_strategy))
        return {
            "ok": False,
            "normalized_url": normalized_url,
            "timestamp": timestamp,
            "updates": updates,
            "result_item": {
                "来源链接": normalized_url,
                "页面探测状态": classification["页面探测状态"],
                "页面探测时间": timestamp,
                "是否值得发帖": classification["是否值得发帖"],
                "评论区是否存在": audit_result.get("has_comment_area", ""),
                "是否需要登录": audit_result.get("requires_login", ""),
                "是否支持Google登录": audit_result.get("supports_google_login", ""),
                "最终链接格式": capability.get("final_result", {}).get("recommended_format", "unknown"),
                "推荐策略": recommended_strategy,
                "页面探测失败原因": classification["页面探测失败原因"],
            },
        }
    finally:
        for path in (task_path, result_path):
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass


def _probe_worker_main(task_file: str, result_file: str) -> int:
    task_payload = json.loads(Path(task_file).read_text(encoding="utf-8"))
    result = _run_probe_worker(task_payload)
    Path(result_file).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


def classify_probe_outcome(
    url: str,
    audit_result: dict,
    capability: dict,
    preprobe_meta: dict,
    excluded_domains: set[str],
    excluded_urls: set[str],
    existing_probe: dict | None = None,
) -> dict:
    light_decision = assess_light_probe(
        url,
        audit_result,
        preprobe_meta,
        excluded_domains,
        excluded_urls,
    )
    if _should_inherit_existing_probe(preprobe_meta, capability):
        inherited = _classification_from_existing_probe(existing_probe)
        if inherited:
            return inherited
    if light_decision["classification"]:
        return light_decision["classification"]

    strong_positive = _has_strong_positive_signals(audit_result, preprobe_meta)
    capability_status = str(capability.get("status", "") or "").strip()
    final_result = capability.get("final_result", {}) or {}
    evidence_type = str(final_result.get("evidence_type", "") or "").strip()

    if capability_status == "failed":
        if strong_positive and _is_promotable_capability_result(capability):
            return _build_probe_classification(PROBE_STATUS_COMPLETED, PROBE_WORTH_YES, "")
        return _build_probe_classification(
            PROBE_STATUS_FAILED,
            PROBE_WORTH_REVIEW,
            evidence_type or "格式探测失败",
        )

    if capability_status == "conflict":
        if strong_positive:
            return _build_probe_classification(PROBE_STATUS_COMPLETED, PROBE_WORTH_YES, "")
        return _build_probe_classification(
            PROBE_STATUS_COMPLETED,
            PROBE_WORTH_REVIEW,
            "格式检测存在冲突，建议复核",
        )

    return _build_probe_classification(PROBE_STATUS_COMPLETED, PROBE_WORTH_YES, "")


def build_page_probe_update(row: dict, classification: dict, timestamp: str, recommended_strategy: str) -> dict:
    return {
        "页面探测状态": classification["页面探测状态"],
        "页面探测时间": timestamp,
        "是否值得发帖": classification["是否值得发帖"],
        "页面探测失败原因": classification["页面探测失败原因"],
        "推荐策略": recommended_strategy,
    }


def summarize_results(results: list[dict]) -> dict:
    probe_status_counts = Counter()
    worth_counts = Counter()
    strategy_counts = Counter()
    for item in results:
        probe_status_counts[item.get("页面探测状态", PROBE_STATUS_PENDING)] += 1
        worth_counts[item.get("是否值得发帖", "") or ""] += 1
        strategy_counts[item.get("推荐策略", "") or "dom"] += 1
    return {
        "total_scanned": len(results),
        "页面探测状态分布": dict(probe_status_counts),
        "是否值得发帖分布": dict(worth_counts),
        "推荐策略分布": dict(strategy_counts),
    }


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "来源链接",
        "页面探测状态",
        "页面探测时间",
        "是否值得发帖",
        "评论区是否存在",
        "是否需要登录",
        "是否支持Google登录",
        "最终链接格式",
        "推荐策略",
        "页面探测失败原因",
        "comment_entry_found",
        "comment_entry_expanded",
        "comment_form_visible",
        "login_wall_after_expand",
        "challenge_after_expand",
        "comment_closed_after_expand",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({header: row.get(header, "") for header in headers})


def _write_change_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        fieldnames = ["来源链接", "旧是否值得发帖", "新是否值得发帖", "旧失败原因", "新失败原因"]
    else:
        fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def load_focus_urls(path: str) -> set[str]:
    if not path:
        return set()
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"focus file not found: {file_path}")
    raw = file_path.read_text(encoding="utf-8").strip()
    if not raw:
        return set()
    if file_path.suffix.lower() == ".json":
        payload = json.loads(raw)
        if not isinstance(payload, list):
            raise ValueError("focus json file must be a list of urls")
        items = [str(item) for item in payload]
    else:
        items = [line.strip() for line in raw.splitlines()]
    focus_urls = set()
    for item in items:
        normalized = normalize_source_url(item)
        if not normalized:
            continue
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        focus_urls.add(normalized)
    return focus_urls


def _build_change_entry(previous: Optional[dict], current: dict) -> dict:
    previous = previous or {}
    return {
        "来源链接": current.get("来源链接", ""),
        "旧页面探测状态": previous.get("页面探测状态", ""),
        "新页面探测状态": current.get("页面探测状态", ""),
        "旧是否值得发帖": previous.get("是否值得发帖", ""),
        "新是否值得发帖": current.get("是否值得发帖", ""),
        "旧失败原因": previous.get("页面探测失败原因", ""),
        "新失败原因": current.get("页面探测失败原因", ""),
        "评论区是否存在": current.get("评论区是否存在", ""),
        "是否需要登录": current.get("是否需要登录", ""),
        "最终链接格式": current.get("最终链接格式", ""),
        "推荐策略": current.get("推荐策略", ""),
    }


def run_probe(
    limit: Optional[int] = None,
    reset_state: bool = False,
    force: bool = False,
    config_path: str = "config.json",
    worth_filter: str = "",
    focus_urls: Optional[set[str]] = None,
) -> dict:
    config = load_probe_task_config(config_path)
    ensure_probe_browser_ready(config)
    state_path = Path(config["state_file"])
    summary_path = Path(config["summary_file"])
    report_json_path = Path(config["report_json_file"])
    report_csv_path = Path(config["report_csv_file"])
    reclassified_yes_json_path = Path(config["reclassified_to_yes_json_file"])
    reclassified_yes_csv_path = Path(config["reclassified_to_yes_csv_file"])
    reason_corrected_json_path = Path(config["reason_corrected_only_json_file"])
    reason_corrected_csv_path = Path(config["reason_corrected_only_csv_file"])
    still_review_json_path = Path(config["still_review_after_reprobe_json_file"])
    still_review_csv_path = Path(config["still_review_after_reprobe_csv_file"])
    state = {} if reset_state else load_json(state_path, {"processed_urls": [], "results": []})
    processed_urls = set(state.get("processed_urls", []))
    prior_results = list(state.get("results", []))
    prior_results_by_url = {}
    for item in prior_results:
        normalized = normalize_source_url(item.get("来源链接", ""))
        if normalized:
            prior_results_by_url[normalized] = item

    workbook = FeishuWorkbook.from_config(config_path)
    if not workbook:
        raise RuntimeError("飞书未正确配置，无法执行来源页面探测。")

    target_rows = read_target_rows(workbook)
    target_domains = extract_active_target_domains(target_rows)
    source_headers = dynamic_source_headers(target_rows)
    workbook.ensure_sheet_headers("sources", source_headers)
    source_rows = read_source_rows_paged(
        workbook,
        max_cols=max(len(source_headers), 250),
        page_size=int(config.get("read_page_size", 250)),
    )
    excluded_domains = _normalize_excluded_domains(config.get("excluded_source_domains", []))
    excluded_urls = _normalize_excluded_urls(config.get("excluded_source_urls", []))
    use_isolated_worker = bool(config.get("isolated_probe_worker", True))

    processed_this_run = []
    reclassified_to_yes = []
    reason_corrected_only = []
    still_review_after_reprobe = []
    scanned = 0

    for row in source_rows:
        normalized_url = normalize_source_url(
            extract_cell_url(row.get("来源链接", "")) or extract_cell_text(row.get("来源链接", ""))
        )
        if not normalized_url:
            continue
        if focus_urls and normalized_url not in focus_urls:
            continue
        existing_probe_status = str(extract_cell_text(row.get("页面探测状态", "")) or "").strip()
        existing_worth = str(extract_cell_text(row.get("是否值得发帖", "")) or "").strip()
        if worth_filter and existing_worth != worth_filter:
            continue
        if not force and existing_probe_status in {PROBE_STATUS_COMPLETED, PROBE_STATUS_FAILED, PROBE_STATUS_SKIPPED}:
            continue
        if not force and normalized_url in processed_urls:
            continue

        print(f"🔎 [{len(processed_urls) + 1}] 开始统一探测: {normalized_url}")
        previous_item = prior_results_by_url.get(normalized_url)
        worker_payload = _build_probe_worker_payload(
            row=row,
            normalized_url=normalized_url,
            existing_probe=_build_existing_probe_snapshot(row, previous_item),
            config=config,
            excluded_domains=excluded_domains,
            excluded_urls=excluded_urls,
            target_domains=target_domains,
        )
        if use_isolated_worker:
            worker_result = _run_probe_row_via_subprocess(worker_payload, config)
        else:
            worker_result = _run_probe_worker(worker_payload)

        timestamp = worker_result["timestamp"]
        updates = worker_result["updates"]
        result_item = worker_result["result_item"]

        write_source_row_with_retry(
            workbook,
            source_headers,
            int(row.get("_row_index", 0) or 0),
            updates,
            max_retries=int(config["write_retry_count"]),
            base_sleep=float(config["write_retry_base_seconds"]),
        )

        processed_urls.add(normalized_url)
        processed_this_run.append(result_item)
        prior_results_by_url[normalized_url] = result_item
        scanned += 1

        previous_worth = str((previous_item or {}).get("是否值得发帖", "") or "").strip()
        current_worth = str(result_item.get("是否值得发帖", "") or "").strip()
        previous_reason = str((previous_item or {}).get("页面探测失败原因", "") or "").strip()
        current_reason = str(result_item.get("页面探测失败原因", "") or "").strip()
        if current_worth == PROBE_WORTH_YES and previous_worth in {PROBE_WORTH_NO, PROBE_WORTH_REVIEW}:
            reclassified_to_yes.append(_build_change_entry(previous_item, result_item))
        elif current_worth == PROBE_WORTH_NO and previous_worth == PROBE_WORTH_NO and current_reason != previous_reason:
            reason_corrected_only.append(_build_change_entry(previous_item, result_item))
        if current_worth == PROBE_WORTH_REVIEW:
            still_review_after_reprobe.append(_build_change_entry(previous_item, result_item))

        current_results = list(prior_results_by_url.values())

        save_json(
            state_path,
            {
                "processed": len(processed_urls),
                "processed_urls": sorted(processed_urls),
                "results": current_results,
                "last_scanned_at": timestamp,
                "last_url": normalized_url,
            },
        )
        current_summary = summarize_results(current_results)
        save_json(summary_path, current_summary)
        save_json(report_json_path, current_results)
        _write_csv(report_csv_path, current_results)
        if limit and scanned >= limit:
            break

    all_results = list(prior_results_by_url.values())
    summary = summarize_results(all_results)
    save_json(summary_path, summary)
    save_json(report_json_path, all_results)
    _write_csv(report_csv_path, all_results)
    save_json(reclassified_yes_json_path, reclassified_to_yes)
    _write_change_csv(reclassified_yes_csv_path, reclassified_to_yes)
    save_json(reason_corrected_json_path, reason_corrected_only)
    _write_change_csv(reason_corrected_csv_path, reason_corrected_only)
    save_json(still_review_json_path, still_review_after_reprobe)
    _write_change_csv(still_review_csv_path, still_review_after_reprobe)
    return {
        "processed_this_run": scanned,
        "total_processed": len(all_results),
        "summary": summary,
        "state_file": str(state_path),
        "summary_file": str(summary_path),
        "report_json_file": str(report_json_path),
        "report_csv_file": str(report_csv_path),
        "reclassified_to_yes_json_file": str(reclassified_yes_json_path),
        "reason_corrected_only_json_file": str(reason_corrected_json_path),
        "still_review_after_reprobe_json_file": str(still_review_json_path),
    }


def main():
    parser = argparse.ArgumentParser(description="批量执行来源页面探测，并写回统一可发性状态。")
    parser.add_argument("--limit", type=int, default=None, help="本次最多扫描多少条来源链接。")
    parser.add_argument("--reset-state", action="store_true", help="忽略断点状态，从头开始扫描。")
    parser.add_argument("--force", action="store_true", help="即使已有页面探测状态，也重新探测。")
    parser.add_argument("--worth-filter", default="", help="仅重跑当前“是否值得发帖”命中的来源，例如：否")
    parser.add_argument("--focus-file", default="", help="只重跑该文件中的 URL 清单（txt/json）。")
    parser.add_argument("--config", default="config.json", help="配置文件路径。")
    parser.add_argument("--worker-task-file", default="", help=argparse.SUPPRESS)
    parser.add_argument("--worker-result-file", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.worker_task_file and args.worker_result_file:
        raise SystemExit(_probe_worker_main(args.worker_task_file, args.worker_result_file))

    result = run_probe(
        limit=args.limit,
        reset_state=args.reset_state,
        force=args.force,
        config_path=args.config,
        worth_filter=args.worth_filter,
        focus_urls=load_focus_urls(args.focus_file),
    )
    print("✅ 来源页面统一探测完成")
    print(f"   本次处理: {result['processed_this_run']}")
    print(f"   累计处理: {result['total_processed']}")
    print(f"   汇总文件: {result['summary_file']}")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
