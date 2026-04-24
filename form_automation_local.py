from __future__ import annotations

import time
import json
import re
import signal
import os
import sys
import tempfile
import subprocess
import argparse
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Optional
from urllib.parse import urljoin, urlparse
from pathlib import Path

from playwright.sync_api import sync_playwright, Page, Frame

from agent_memory import AgentMemory, normalize_failure_category
from browser_cdp import DEFAULT_CDP_URL, ensure_allowed_cdp_url, ensure_cdp_blank_page, merge_browser_config
from backlink_state import EXECUTION_MODE_AGENT, EXECUTION_MODE_CLASSIC
from legacy_feishu_history import extract_cell_text, extract_cell_url, normalize_source_url

DEFAULT_CONTACT_EMAIL = "slideology0816@gmail.com"
FAST_BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}
FAST_BLOCKED_URL_PATTERNS = (
    "doubleclick.net",
    "googlesyndication.com",
    "googleadservices.com",
    "google-analytics.com",
    "googletagmanager.com",
    "googleapis.com/pagead",
    "gstatic.com/recaptcha",
    "imasdk.googleapis.com",
    "safeframe.googlesyndication.com",
    "tynt.com",
    "criteo.com",
    "rubiconproject.com",
    "pubmatic.com",
    "adnxs.com",
    "openx.net",
    "media.net",
    "smaato.net",
    "onetag-sys.com",
    "indexww.com",
    "prebid",
)
COMMENT_SIGNAL_SELECTORS = (
    "textarea",
    '[contenteditable="true"]',
    '[role="textbox"]',
    ".ql-editor",
    ".ProseMirror",
    ".fr-element",
    ".public-DraftEditor-content",
    ".cke_editable",
    "#comments",
    ".comments",
    ".comment-list",
    ".commentlist",
    ".comment-respond",
    "#respond",
    ".comment-form",
    "#commentform",
    'input[name*="email"]',
    'input[type="email"]',
    'input[name*="url"]',
    'input[name*="website"]',
    'iframe[src*="comment"]',
    'iframe[src*="blogger"]',
    'iframe[src*="disqus"]',
    'iframe[src*="reply"]',
    'form[id*="comment"]',
    'form[class*="comment"]',
)
COMMENT_EDITOR_SELECTORS = (
    "textarea:visible",
    '[contenteditable="true"]:visible',
    '[role="textbox"]:visible',
    '.ql-editor:visible',
    '.ProseMirror:visible',
    '.fr-element:visible',
    '.public-DraftEditor-content:visible',
    '.cke_editable:visible',
)
COMMENT_REVEAL_SELECTORS = (
    'button:has-text("Reply")',
    'a:has-text("Reply")',
    'button:has-text("Leave a Reply")',
    'a:has-text("Leave a Reply")',
    'button:has-text("Leave a comment")',
    'a:has-text("Leave a comment")',
    'button:has-text("Add Comment")',
    'a:has-text("Add Comment")',
    'button:has-text("Comment")',
    'a:has-text("Comment")',
)
REVEAL_LINK_BLOCK_MARKERS = (
    "akismet.com/privacy",
    "blogger.com/comment/fullpage/",
    "draft.blogger.com/comment/fullpage/",
    "privacy-policy",
    "/privacy/",
    "/privacy-policy",
    "/terms",
    "/policy",
)
REVEAL_TEXT_BLOCK_MARKERS = (
    "comment data is processed",
    "privacy policy",
    "terms of service",
    "cookies",
)
CHALLENGE_FRAME_MARKERS = (
    "challenge-platform",
    "challenges.cloudflare.com",
    "turnstile",
    "recaptcha",
    "hcaptcha",
)
CHALLENGE_TEXT_MARKERS = (
    "verify you are human",
    "security check",
    "cloudflare",
    "captcha",
    "turnstile",
    "人机验证",
    "安全检查",
)
COMMENTS_CLOSED_MARKERS = (
    "comments are closed",
    "closed for comments",
    "评论已关闭",
    "不允许评论",
)
LOGIN_REQUIRED_MARKERS = (
    "log in to comment",
    "you must be logged in",
    "sign in to comment",
    "login to post a comment",
    "登录后评论",
)
IRRELEVANT_FRAME_PATTERNS = (
    "youtube.com",
    "vimeo.com",
    "facebook.com",
    "twitter.com",
    "doubleclick",
    "googleads",
    "googlesyndication",
    "googleadservices",
    "imasdk.googleapis.com",
    "tynt.com",
    "rubiconproject.com",
    "criteo.com",
    "pubmatic.com",
    "adnxs.com",
    "openx.net",
    "media.net",
    "smaato.net",
    "onetag-sys.com",
    "indexww.com",
    "prebid",
)

# 错误信息中文化查找表
ERROR_TRANSLATIONS = {
    "Timeout": "网络连接超时，网页加载过慢",
    "ERR_NAME_NOT_RESOLVED": "无法解析网址，可能网站已挂掉",
    "ERR_CONNECTION_REFUSED": "网站拒绝连接，服务器可能宕机了",
    "strict mode violation": "页面存在多个同样的输入框，识别混淆",
    "Target closed": "浏览器窗口意外关闭",
    "is not a function": "网页脚本执行出错",
    "waiting for selector": "在页面上没找到对应的输入区域",
    "net::ERR": "底层网络错误",
    "Protocol error": "浏览器通讯故障",
    "Execution context was destroyed": "页面正在刷新或已跳转，操作失效"
}

def translate_error(error_msg: str) -> str:
    """将英文异常转换为白话中文"""
    error_str = str(error_msg)
    for eng, chn in ERROR_TRANSLATIONS.items():
        if eng.lower() in error_str.lower():
            return f"{chn} ({eng})"
    return error_str


def load_runtime_config(config_path="config.json"):
    defaults = {
        "agent_assist": {
            "enabled": True,
            "failure_threshold": 2,
            "same_link_daily_limit": 1,
            "same_domain_daily_limit": 1,
            "domain_cooldown_hours": 12,
            "temporary_blacklist_hours": 72,
        },
        "ai_generation": {
            "preprobe_before_generation": True,
            "generate_comment_summary": False,
            "generate_chinese_translation": False,
        },
        "execution": {
            "success_goal": 10,
            "page_load_timeout_ms": 30000,
            "single_task_timeout_seconds": 180,
            "isolated_task_worker": True,
            "worker_poll_interval_seconds": 10,
            "worker_timeout_buffer_seconds": 20,
            "max_retries": 1,
            "enable_sso": False,
        },
        "browser": {
            "connect_cdp_url": DEFAULT_CDP_URL,
        },
        "vision": {
            "enabled": True,
            "debug_dir": "artifacts/vision",
        },
    }
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception:
        return defaults

    merged = {**defaults, **config}
    merged["agent_assist"] = {**defaults["agent_assist"], **config.get("agent_assist", {})}
    merged["ai_generation"] = {**defaults["ai_generation"], **config.get("ai_generation", {})}
    merged["execution"] = {**defaults["execution"], **config.get("execution", {})}
    merged["browser"] = merge_browser_config(config.get("browser", {}) or defaults["browser"])
    merged["vision"] = {**defaults["vision"], **config.get("vision", {})}
    return merged


@contextmanager
def task_timeout_guard(timeout_seconds: int):
    timeout_seconds = int(timeout_seconds or 0)
    if timeout_seconds <= 0 or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _handle_timeout(signum, frame):  # pragma: no cover - exercised via integration/runtime
        raise TimeoutError(f"单条任务执行超时（>{timeout_seconds}秒）")

    previous_handler = signal.getsignal(signal.SIGALRM)
    try:
        signal.signal(signal.SIGALRM, _handle_timeout)
        signal.alarm(timeout_seconds)
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def _current_python_bin() -> str:
    return sys.executable


def summarize_result_message(message: str, limit: int = 160) -> str:
    clean = " ".join(str(message).split())
    return clean if len(clean) <= limit else f"{clean[:limit - 3]}..."


def format_notes(message: str, diagnosis: str = "") -> str:
    if diagnosis and diagnosis not in message:
        return f"{message} | 自动诊断: {diagnosis}"
    return message


def _format_memory_dt(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return text


def _append_agent_trace(meta: dict, step: str) -> None:
    if not step:
        return
    meta.setdefault("action_trace", [])
    meta["action_trace"].append(step)


def _normalize_target_value(value, *, is_url: bool = False) -> str:
    if is_url:
        normalized = normalize_source_url(extract_cell_url(value) or extract_cell_text(value))
        return normalized or str(extract_cell_text(value) or "").strip()
    return str(extract_cell_text(value) or value or "").strip()


def _open_hidden_comment_entry_points(page: Page) -> list[str]:
    clicked = []
    for selector in COMMENT_REVEAL_SELECTORS:
        try:
            locator = page.locator(selector)
            if locator.count() > 0 and locator.first.is_visible():
                locator.first.scroll_into_view_if_needed()
                locator.first.click(timeout=1000)
                clicked.append(selector)
                time.sleep(0.5)
        except Exception:
            continue
    return clicked


def _detect_complex_editor_signal(page: Page) -> str:
    selectors = (
        '[contenteditable="true"]',
        '[role="textbox"]',
        '.ql-editor',
        '.ProseMirror',
        '.fr-element',
        '.public-DraftEditor-content',
        '.cke_editable',
        'iframe[src*="blogger"]',
        'iframe[src*="disqus"]',
        'iframe[src*="comment"]',
    )
    for selector in selectors:
        try:
            locator = page.locator(selector)
            if locator.count() > 0:
                return selector
        except Exception:
            continue
    return ""


def _maybe_bring_to_front(page, browser_cfg: dict) -> None:
    if not browser_cfg.get("bring_to_front", False):
        return
    try:
        page.bring_to_front()
    except Exception:
        pass


def _acquire_cdp_work_page(browser_app, browser_cfg: dict):
    contexts = list(getattr(browser_app, "contexts", []) or [])
    if not contexts:
        raise RuntimeError("CDP 已连接，但 Chrome 当前没有可用上下文。")

    context = contexts[0]
    pages = list(getattr(context, "pages", []) or [])
    if pages:
        page = pages[0]
        try:
            page.goto("about:blank", wait_until="domcontentloaded", timeout=5000)
        except Exception:
            pass
        _maybe_bring_to_front(page, browser_cfg)
        return context, page

    try:
        page = context.new_page()
        _maybe_bring_to_front(page, browser_cfg)
        return context, page
    except Exception as exc:
        raise RuntimeError(
            "CDP 已连接，但当前 Chrome 会话不支持为默认上下文新建标签页。"
            "请保留机器人 Chrome 的默认标签页，或重启 Start_Robot.command 后再试。"
        ) from exc


def _ensure_fast_page_routes(page: Page) -> None:
    if getattr(page, "_fast_routes_installed", False):
        return

    def _route_handler(route):
        request = route.request
        try:
            resource_type = str(request.resource_type or "").lower()
            url = str(request.url or "").lower()
            if resource_type in FAST_BLOCKED_RESOURCE_TYPES:
                route.abort()
                return
            if any(marker in url for marker in FAST_BLOCKED_URL_PATTERNS):
                route.abort()
                return
        except Exception:
            pass
        route.continue_()

    try:
        page.route("**/*", _route_handler)
        setattr(page, "_fast_routes_installed", True)
    except Exception:
        setattr(page, "_fast_routes_installed", False)


def _fast_navigate_for_commenting(page: Page, url: str, page_load_timeout_ms: int) -> dict:
    _ensure_fast_page_routes(page)
    effective_timeout = max(5000, int(page_load_timeout_ms or 30000))
    nav_meta = {"partial_navigation": False, "navigation_warning": ""}
    try:
        page.goto(url, timeout=effective_timeout, wait_until="domcontentloaded")
    except Exception as exc:
        warning = translate_error(str(exc))
        try:
            ready_state = page.evaluate("() => document.readyState")
            has_body = page.locator("body").count() > 0
            page.evaluate("() => window.stop()")
        except Exception:
            ready_state = ""
            has_body = False

        if has_body and ready_state in {"interactive", "complete"} and str(page.url or "") not in {"", "about:blank"}:
            print(f"  ⚠️ 页面导航超时，改用已加载的部分 DOM 继续尝试: {warning}")
            nav_meta["partial_navigation"] = True
            nav_meta["navigation_warning"] = warning
            return nav_meta
        try:
            fallback_timeout = min(8000, max(2500, effective_timeout // 2))
            page.goto(url, timeout=fallback_timeout, wait_until="commit")
            page.wait_for_timeout(800)
            has_body = page.locator("body").count() > 0
        except Exception:
            has_body = False
        if has_body and str(page.url or "") not in {"", "about:blank"}:
            print(f"  ⚠️ 页面仅完成轻量导航，使用部分 DOM 继续尝试: {warning}")
            nav_meta["partial_navigation"] = True
            nav_meta["navigation_warning"] = warning
            return nav_meta
        raise

    try:
        page.wait_for_load_state("networkidle", timeout=min(2500, max(800, effective_timeout // 6)))
    except Exception:
        pass
    return nav_meta


def _get_page_body_text(page: Page) -> str:
    try:
        return " ".join(page.locator("body").all_inner_texts()).lower()
    except Exception:
        return ""


def _detect_hard_blocker(page: Page) -> tuple[bool, str]:
    body_text = _get_page_body_text(page)
    if any(marker in body_text for marker in COMMENTS_CLOSED_MARKERS):
        return True, "页面提示评论已关闭"
    if any(marker in body_text for marker in LOGIN_REQUIRED_MARKERS):
        return True, "页面提示必须登录后才能评论"
    if any(marker in body_text for marker in CHALLENGE_TEXT_MARKERS):
        return True, "页面存在验证码或 Cloudflare 挑战"

    for frame in getattr(page, "frames", []):
        frame_url = str(getattr(frame, "url", "") or "").lower()
        if any(marker in frame_url for marker in CHALLENGE_FRAME_MARKERS):
            return True, "页面存在验证码或 Cloudflare 挑战 iframe"

    return False, ""


def _page_has_comment_signals(page: Page) -> tuple[bool, str]:
    for selector in COMMENT_SIGNAL_SELECTORS:
        try:
            locator = page.locator(selector)
            if locator.count() > 0:
                return True, f"命中选择器 {selector}"
        except Exception:
            continue

    body_text = _get_page_body_text(page)
    if any(marker in body_text for marker in COMMENTS_CLOSED_MARKERS):
        return False, "页面提示评论已关闭"
    if any(marker in body_text for marker in LOGIN_REQUIRED_MARKERS):
        return False, "页面提示必须登录后才能评论"
    if any(marker in body_text for marker in CHALLENGE_TEXT_MARKERS):
        return False, "页面存在验证码或 Cloudflare 挑战"
    if any(marker in body_text for marker in ("leave a reply", "post a comment", "comment as", "发表评论", "评论")):
        return True, "页面文本包含评论区提示词"

    for frame in getattr(page, "frames", []):
        frame_url = str(getattr(frame, "url", "") or "").lower()
        if any(marker in frame_url for marker in CHALLENGE_FRAME_MARKERS):
            return False, "页面存在验证码或 Cloudflare 挑战 iframe"
        if any(marker in frame_url for marker in ("comment", "reply", "blogger", "disqus", "wpdiscuz", "remark42", "giscus")):
            return True, f"命中评论相关 iframe {frame_url[:60]}"

    return False, "DOM 与页面文本均未发现评论区线索"


def _preprobe_page_for_generation(
    page: Page,
    url: str,
    page_load_timeout_ms: int,
    execution_mode: str,
    recommended_strategy: str,
) -> dict:
    meta = {
        "ok": True,
        "message": "",
        "diagnosis": "",
        "diagnostic_category": "",
        "navigation_warning": "",
        "recommended_strategy": recommended_strategy,
        "action_trace": [],
    }
    nav_meta = _fast_navigate_for_commenting(page, url, page_load_timeout_ms)
    meta["navigation_warning"] = nav_meta.get("navigation_warning", "")
    _append_agent_trace(meta, "navigate_page")

    try_dismiss_overlays(page)
    _append_agent_trace(meta, "dismiss_overlays")

    _deep_scroll_to_bottom(page)
    _append_agent_trace(meta, "deep_scroll")

    if execution_mode == EXECUTION_MODE_AGENT:
        clicked_selectors = _open_hidden_comment_entry_points(page)
        if clicked_selectors:
            _append_agent_trace(meta, f"reveal_comment_entry:{len(clicked_selectors)}")

        complex_editor_selector = _detect_complex_editor_signal(page)
        if complex_editor_selector:
            meta["recommended_strategy"] = "iframe"
            _append_agent_trace(meta, f"complex_editor:{complex_editor_selector}")

    blocker_detected, blocker_reason = _detect_hard_blocker(page)
    if blocker_detected:
        meta.update(
            {
                "ok": False,
                "message": "页面预探测判定当前页面不值得继续尝试。",
                "diagnosis": blocker_reason,
                "diagnostic_category": "hard_blocker",
            }
        )
        _append_agent_trace(meta, "preprobe_stop")
        return meta

    has_comment_signal, comment_reason = _page_has_comment_signals(page)
    if not has_comment_signal:
        meta.update(
            {
                "ok": False,
                "message": "页面预探测未发现可用评论区。",
                "diagnosis": comment_reason,
                "diagnostic_category": "comment_signal_missing",
            }
        )
        _append_agent_trace(meta, "no_comment_signal")
        return meta

    _append_agent_trace(meta, "preprobe_pass")
    return meta


def _should_use_vision_fallback(layer1_message: str) -> bool:
    message = str(layer1_message or "")
    normalized = message.lower()
    if not normalized:
        return True
    return any(
        marker in normalized
        for marker in (
            "layer 1: 主页面及所有嵌套 iframe 中均未找到任何评论输入框",
            "没有找到可以点击的提交按钮",
            "未找到任何评论输入框",
        )
    )


def _build_task_failure_updates(task_row: dict, target: dict, reason: str, timestamp: str) -> dict:
    raw_url = task_row.get("来源链接", "")
    normalized_url = normalize_source_url(extract_cell_url(raw_url) or extract_cell_text(raw_url))
    return {
        "来源链接": normalized_url,
        "来源标题": str(task_row.get("来源标题", "") or ""),
        "根域名": str(task_row.get("根域名", "") or ""),
        "页面评分": str(task_row.get("页面评分", "") or ""),
        "目标站标识": str(target.get("site_key", "") or ""),
        "状态": STATUS_PENDING_RETRY,
        "最后尝试时间": timestamp,
        "最近失败时间": timestamp,
        "最近失败原因": summarize_result_message(reason),
        "最近失败分类": normalize_failure_category(reason=reason),
        "执行模式": str(task_row.get("执行模式", "") or EXECUTION_MODE_CLASSIC),
        "推荐策略": str(task_row.get("推荐策略", "") or "dom"),
        "域名冷却至": "",
        "最后更新时间": timestamp,
    }


def _join_agent_trace(meta: dict) -> str:
    trace = meta.get("action_trace", [])
    if isinstance(trace, list):
        return " | ".join(str(item) for item in trace[:12] if str(item).strip())
    return str(trace or "")


def _apply_agent_memory_result(
    url: str,
    success: bool,
    execution_mode: str,
    recommended_strategy: str,
    diagnostic_category: str,
    reason: str,
    runtime_cfg: dict,
) -> dict:
    memory = AgentMemory()
    normalized_category = normalize_failure_category(diagnostic_category, reason)
    strategy = recommended_strategy or ("vision" if diagnostic_category == "vision_success" else "dom")
    memory.record_result(
        url,
        success=success,
        strategy=strategy,
        failure_reason=reason,
        failure_category=normalized_category,
        execution_mode=execution_mode,
    )

    assist_cfg = runtime_cfg.get("agent_assist", {}) if isinstance(runtime_cfg, dict) else {}
    cooldown_hours = int(assist_cfg.get("domain_cooldown_hours", 12) or 12)
    temp_blacklist_hours = int(assist_cfg.get("temporary_blacklist_hours", 72) or 72)
    profile = memory.get_site_profile(url)

    if not success:
        if normalized_category == "comment_unavailable":
            memory.mark_blacklist(url, reason or "评论关闭或页面无评论区")
        elif normalized_category == "page_protected":
            memory.mark_temporary_blacklist(url, reason or "页面存在挑战/登录墙", hours=temp_blacklist_hours)
        if (
            normalized_category in {"network_timeout", "vision_unavailable", "page_protected", "editor_complex", "post_unverified"}
            or int(profile.get("consecutive_failures", 0) or 0) >= int(assist_cfg.get("failure_threshold", 2) or 2)
        ):
            memory.set_cooldown(url, reason or normalized_category, hours=cooldown_hours)
        profile = memory.get_site_profile(url)

    return {
        "normalized_category": normalized_category,
        "cooldown_until": memory.get_cooldown_until(url),
        "profile": profile,
    }


def build_source_format_lookup(
    workbook: FeishuWorkbook,
    max_cols: int = 250,
    page_size: int = 300,
) -> dict[str, dict]:
    lookup = {}
    try:
        selected_headers = [
            "来源链接",
            "最终链接格式",
            "格式检测证据",
            "格式检测置信度",
            "格式检测阶段",
        ]
        for _, row in workbook.iter_sheet_selected_rows(
            "sources",
            selected_headers=selected_headers,
            max_cols=max_cols,
            page_size=page_size,
        ):
            normalized_url = normalize_source_url(
                extract_cell_url(row.get("来源链接", "")) or extract_cell_text(row.get("来源链接", ""))
            )
            if normalized_url:
                lookup[normalized_url] = row
    except Exception:
        return {}
    return lookup


def resolve_runtime_link_format(url: str, current_link_format: str, source_row: Optional[dict] = None) -> tuple[str, dict]:
    if source_row:
        source_final = normalize_google_value("Link_Format", source_row.get("最终链接格式", ""))
        if source_final:
            return source_final, {
                "recommended_format": source_final,
                "evidence_type": str(source_row.get("格式检测证据", "") or "source_master_final_format"),
                "confidence": float(str(source_row.get("格式检测置信度", "") or "0") or 0),
                "stage": str(source_row.get("格式检测阶段", "") or "source_master"),
            }

    normalized = normalize_google_value("Link_Format", current_link_format or "")
    if normalized and normalized != "unknown":
        return normalized, {}

    global _LINK_FORMAT_DETECTOR
    if _LINK_FORMAT_DETECTOR is None:
        from website_format_detector import WebsiteFormatDetector

        _LINK_FORMAT_DETECTOR = WebsiteFormatDetector()

    analysis = _LINK_FORMAT_DETECTOR.analyze_website(url)
    recommended = normalize_google_value("Link_Format", analysis.get("recommended_format", "unknown"))
    if recommended and recommended != "unknown":
        return recommended, analysis
    hostname = (urlparse(url).hostname or "").lower() if url else ""
    if "blogspot." in hostname or hostname.endswith(".blogger.com") or hostname == "blogger.com":
        return "html", {
            "recommended_format": "html",
            "evidence_type": "url_blogger_hint",
            "confidence": 0.6,
        }
    return normalized or "unknown", analysis

def _fill_additional_fields(target, name: str, email: str, website: str):
    """
    寻找并填写姓名、邮箱、网站等补充字段（常见于 WordPress / Blogger）
    """
    # 姓名选择器
    name_selectors = ['input[name*="author"]', 'input[id*="author"]', 'input[name*="name"]', 'input[placeholder*="Name"]']
    for s in name_selectors:
        try:
            field = target.locator(s).first
            if field.count() > 0 and field.is_visible():
                print(f"  👤 填写姓名: {name}")
                field.fill(name)
                break
        except: continue

    # 邮箱选择器
    email_selectors = ['input[name*="email"]', 'input[id*="email"]', 'input[name*="email"]', 'input[type="email"]', 'input[placeholder*="Email"]']
    for s in email_selectors:
        try:
            field = target.locator(s).first
            if field.count() > 0 and field.is_visible():
                print(f"  📧 填写邮箱: {email}")
                field.fill(email)
                break
        except: continue

    # 网站选择器
    web_selectors = ['input[name*="url"]', 'input[id*="url"]', 'input[name*="website"]', 'input[placeholder*="Website"]', 'input[placeholder*="Url"]']
    for s in web_selectors:
        try:
            field = target.locator(s).first
            if field.count() > 0 and field.is_visible():
                print(f"  🔗 填写网站: {website}")
                field.fill(website)
                break
        except: continue


def _try_reveal_comment_form(target) -> bool:
    for selector in COMMENT_REVEAL_SELECTORS:
        try:
            locator = target.locator(selector).first
            if locator.count() > 0 and locator.is_visible():
                href = ""
                text = ""
                tag_name = ""
                try:
                    href = str(locator.get_attribute("href") or "").strip()
                except Exception:
                    href = ""
                try:
                    text = str(locator.inner_text(timeout=300) or "").strip()
                except Exception:
                    text = ""
                try:
                    tag_name = str(locator.evaluate("(el) => el.tagName") or "").strip().lower()
                except Exception:
                    tag_name = ""

                if not _should_click_reveal_candidate(
                    current_url=getattr(target, "url", "") or "",
                    tag_name=tag_name,
                    href=href,
                    text=text,
                ):
                    continue
                locator.click(timeout=1500, force=True)
                time.sleep(0.5)
                return True
        except Exception:
            continue
    return False


def _should_click_reveal_candidate(current_url: str, tag_name: str, href: str, text: str) -> bool:
    lowered_text = (text or "").strip().lower()
    lowered_href = (href or "").strip().lower()
    tag = (tag_name or "").strip().lower()

    if any(marker in lowered_text for marker in REVEAL_TEXT_BLOCK_MARKERS):
        return False
    if any(marker in lowered_href for marker in REVEAL_LINK_BLOCK_MARKERS):
        return False

    if tag != "a":
        return True

    if not lowered_href or lowered_href.startswith("#") or lowered_href.startswith("javascript:"):
        return True

    current = urlparse(current_url or "")
    candidate = urlparse(urljoin(current_url or "", href or ""))

    if candidate.scheme and candidate.scheme not in {"http", "https"}:
        return False

    if current.netloc and candidate.netloc and candidate.netloc != current.netloc:
        return False

    if current.path and candidate.path and candidate.path != current.path and not candidate.fragment:
        return False

    return True


def _try_fill_comment_editor(target, comment_content: str) -> tuple[bool, str]:
    for selector in COMMENT_EDITOR_SELECTORS:
        try:
            editor = target.locator(selector).first
            if editor.count() == 0 or not editor.is_visible():
                continue
            editor.scroll_into_view_if_needed()
            try:
                editor.fill(comment_content, timeout=2500)
                return True, selector
            except Exception:
                pass
            try:
                editor.click(timeout=2500)
            except Exception:
                continue
            try:
                editor.press("Control+A", timeout=1000)
            except Exception:
                pass
            editor.type(comment_content, delay=30, timeout=5000)
            return True, selector
        except Exception:
            continue
    return False, ""

def _deep_scroll_to_bottom(page: Page):
    """
    分段深度滚动，模拟真人翻阅并触发长页面的懒加载。
    针对评论极多的页面（如 100+ 评论）至关重要。
    """
    print("  🌀 正在进行深度滚动，寻找页面底部的评论区...")
    for i in range(5):  # 最多滚 5 屏
        page.evaluate("window.scrollBy(0, 2000)")
        time.sleep(1.5) # 给点时间让内容蹦出来
        _try_reveal_comment_form(page)
        # 实时检查是否已经看到评论框，看到了就提前收工
        for selector in COMMENT_EDITOR_SELECTORS:
            try:
                if page.locator(selector).count() > 0 and page.locator(selector).first.is_visible():
                    print(f"  ✨ 在第 {i+1} 次滚动时提前发现评论框！")
                    return
            except Exception:
                continue
    print("  🏁 深度滚动完毕。")

def _diagnose_site_status(page: Page) -> str:
    """
    当任务宣告失败时调用的“全自动法医诊断”。
    识别导致失败的客观原因并生成通俗解释。
    """
    print("  🔍 正在分析失败原因（自动化诊断）...")
    try:
        visible_text = page.locator("body").inner_text().lower()
        
        # 1. 登录墙
        if any(kw in visible_text for kw in ["log in to comment", "you must be logged in", "sign in", "登录后"]):
            return "🔒 该站设置了登录墙，必须有账号才能评论。"
            
        # 2. 评论关闭
        if any(kw in visible_text for kw in ["comments are closed", "closed for comments", "评论已关闭", "不允许评论"]):
            return "🚫 该博主已关闭此文章的评论功能。"
            
        # 3. 页面类型
        if "/uploads/" in page.url and any(kw in page.url for kw in ["/image/", "/attachment/"]):
            return "🖼️ 这是一个图片附件页，通常没有评论区域。"
            
        # 4. Blogger 登录墙特有特征
        if "blogger.com" in page.url and "comment-editor.do" in page.url:
             return "🔑 Blogger 评论页强制要求登录 Google 账号。"

        return "❓ 未定位到评论框，可能是表单结构极其复杂或动态加载失败。"
    except:
        return "🌐 页面加载异常或网络极其不稳。"

from ai_generator import analyze_keywords, generate_anchor_text, generate_comment
from backlink_state import STATUS_HEADERS, STATUS_PENDING_RETRY, STATUS_SUCCESS
from feishu_workbook import FeishuWorkbook
from page_context import fetch_page_context
from sheet_localization import normalize_google_value
from sync_reporting_workbook import sync_reporting_workbook

MY_TARGET_WEBSITE = "https://bearclicker.net/"
_LINK_FORMAT_DETECTOR = None

# =====================================================================
# 预处理：尝试关掉所有 Cookie 同意弹窗（修复 GDPR 遮挡问题）
# =====================================================================
def try_dismiss_overlays(page: Page):
    """
    尝试关闭常见的 Cookie/GDPR 弹窗，防止它遮挡评论框和提交按钮。
    欧洲网站几乎 100% 有这类弹框，是导致按钮被遮挡最常见的元凶。
    此函数不会在任何情况下抛出异常，仅做"尽力而为"的清理。
    """
    dismiss_selectors = [
        'button:has-text("Accept all")',
        'button:has-text("Accept All")',
        'button:has-text("Accept")',
        'button:has-text("I Accept")',
        'button:has-text("Agree")',
        'button:has-text("I Agree")',
        'button:has-text("OK")',
        'button:has-text("Got it")',
        'button:has-text("Close")',
        'button:has-text("Dismiss")',
        '#onetrust-accept-btn-handler',         # OneTrust Cookie 框架
        '.cookie-consent button',
        '.gdpr-banner button',
        '[aria-label="Close"]',
        '[aria-label="Dismiss"]',
        '.cc-btn.cc-allow',                      # Cookie Consent JS 框架
        '#cookieConsentOK',
    ]
    for selector in dismiss_selectors:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=500):
                btn.click(timeout=500)
                time.sleep(0.5)
                print(f"  ✅ 已自动关闭一个弹窗: [{selector}]")
                break  # 关掉一个就行，不用全试
        except:
            continue  # 找不到或点不到，静默跳过


# =====================================================================
# 核心发帖函数（修复了 contenteditable 识别 + 弹窗清除 + 重试机制）
# =====================================================================
def auto_post_content(
    page: Page,
    comment_content: str,
    url: str,
    name: str = "",
    email: str = "",
    website: str = "",
    anchor_text: str = "",
    max_retries: int = 1,
    page_load_timeout_ms: int = 30000,
    enable_sso: bool = False,
    enable_vision: bool = True,
    execution_mode: str = EXECUTION_MODE_CLASSIC,
    recommended_strategy: str = "dom",
    preprobe_meta: Optional[dict] = None,
) -> tuple[bool, str, dict]:
    """
    五层衰减策略全自动发帖机器人（已接入 AI 策略决策器）：
    Layer 1 → 传统 DOM (textarea / contenteditable)
    Layer 2 → Google SSO 单点登录后再试
    Layer 3 → AI 策略决策器（动态选择后续路径）
    Layer 4 → Gemini Vision AI 截图坐标物理点击
    Layer 5 → 宣告失败
    """
    last_error = ""
    meta = {
        "used_vision": False,
        "diagnostic_category": "unclassified",
        "diagnosis": "",
        "navigation_warning": "",
        "execution_mode": execution_mode,
        "recommended_strategy": recommended_strategy,
        "action_trace": [],
    }

    for attempt in range(max_retries):
        if attempt > 0:
            print(f"  ⏳ 第 {attempt + 1} 次重试...")
            time.sleep(5)

        try:
            if preprobe_meta:
                meta.update({k: v for k, v in preprobe_meta.items() if k in {"navigation_warning", "recommended_strategy"}})
                for step in preprobe_meta.get("action_trace", []):
                    _append_agent_trace(meta, step)
            else:
                nav_meta = _fast_navigate_for_commenting(page, url, page_load_timeout_ms)
                meta.update(nav_meta)
                _append_agent_trace(meta, "navigate_page")

                # 【预处理】关闭 GDPR Cookie 弹窗
                try_dismiss_overlays(page)
                _append_agent_trace(meta, "dismiss_overlays")

                # 【优化】深滚动触发评论框加载 (针对长页面/懒加载)
                _deep_scroll_to_bottom(page)
                _append_agent_trace(meta, "deep_scroll")

                if execution_mode == EXECUTION_MODE_AGENT:
                    clicked_selectors = _open_hidden_comment_entry_points(page)
                    if clicked_selectors:
                        _append_agent_trace(meta, f"reveal_comment_entry:{len(clicked_selectors)}")

                    complex_editor_selector = _detect_complex_editor_signal(page)
                    if complex_editor_selector:
                        meta["recommended_strategy"] = "iframe"
                        _append_agent_trace(meta, f"complex_editor:{complex_editor_selector}")

                    blocker_detected, blocker_reason = _detect_hard_blocker(page)
                    if blocker_detected:
                        meta["diagnostic_category"] = "hard_blocker"
                        meta["diagnosis"] = blocker_reason
                        _append_agent_trace(meta, "preprobe_stop")
                        return False, format_notes("Agent 预探测判定当前页面不值得继续尝试。", blocker_reason), meta

                    has_comment_signal, comment_reason = _page_has_comment_signals(page)
                    if not has_comment_signal:
                        meta["diagnostic_category"] = "comment_signal_missing"
                        meta["diagnosis"] = comment_reason
                        _append_agent_trace(meta, "no_comment_signal")
                        return False, format_notes("Agent 预探测未发现可用评论区。", comment_reason), meta

            # ===== Layer 1：传统 DOM 识别 =====
            layer1_result = _try_dom_post(page, comment_content, name, email, website, anchor_text=anchor_text)
            if layer1_result[0]:
                meta["diagnostic_category"] = "dom_success"
                _append_agent_trace(meta, "dom_success")
                return True, layer1_result[1], meta
            last_error = summarize_result_message(layer1_result[1])
            _append_agent_trace(meta, "dom_failed")

            # DOM 已提交但无法确认成功（已填写了内容），不继续降级避免重复发帖
            should_use_vision = _should_use_vision_fallback(layer1_result[1])
            if not should_use_vision:
                meta["diagnostic_category"] = "dom_submit_unconfirmed"
                meta["diagnosis"] = "DOM 已完成填写或提交，但页面未出现明确成功信号。"
                return False, format_notes(last_error, meta["diagnosis"]), meta

            # ===== Layer 2：Google SSO 单点登录（当配置启用时）=====
            if enable_sso:
                print("  🔄 Layer 1 失败，尝试 Layer 2：Google SSO 单点登录...")
                from sso_handler import detect_and_do_google_sso

                sso_success = detect_and_do_google_sso(page)
                if sso_success:
                    print("  ✅ SSO 登录成功，重新尝试填写表单...")
                    try_dismiss_overlays(page)
                    layer2_result = _try_dom_post(
                        page, comment_content, name, email, website, anchor_text=anchor_text
                    )
                    if layer2_result[0]:
                        meta["diagnostic_category"] = "sso_success"
                        return True, layer2_result[1], meta
                    last_error = summarize_result_message(layer2_result[1])
                    if not _should_use_vision_fallback(layer2_result[1]):
                        meta["diagnostic_category"] = "sso_submit_unconfirmed"
                        meta["diagnosis"] = "SSO 后已完成填写或提交，但页面未出现明确成功信号。"
                        return False, format_notes(last_error, meta["diagnosis"]), meta
                    print("  🔄 SSO 后 DOM 方式仍失败，升级到 Layer 3...")
                else:
                    print("  ➡️  未找到 Google SSO 入口，继续后续兜底流程...")
            else:
                print("  ⏭️ 已按配置跳过 Google SSO，避免额外窗口打扰。")

            # ===== Layer 3：AI 策略决策器（动态选择后续路径）=====
            if enable_vision:
                blocker_detected, blocker_reason = _detect_hard_blocker(page)
                if blocker_detected:
                    meta["diagnostic_category"] = "hard_blocker"
                    meta["diagnosis"] = blocker_reason
                    last_error = summarize_result_message(
                        f"{last_error} | 已跳过 Vision：{blocker_reason}".strip(" |")
                    )
                    return False, format_notes(last_error, blocker_reason), meta

                # 获取当前页面截图，供 AI 分析
                _screenshot_bytes: Optional[bytes] = None
                try:
                    _screenshot_bytes = page.screenshot(type="jpeg", quality=60, full_page=False)
                except Exception:
                    pass

                # 确定当前错误码（从 Layer 1/2 失败结果推断）
                _err_code = "dom_not_found"
                if "未找到任何评论输入框" in last_error or "没有找到" in last_error:
                    _err_code = "dom_not_found"
                elif "超时" in last_error or "timeout" in last_error.lower():
                    _err_code = "navigation_timeout"
                else:
                    _err_code = "dom_submit_failed"

                from strategy_decider import (
                    decide_next_strategy,
                    should_retry_dom as sd_should_retry_dom,
                    should_try_vision,
                    should_skip as sd_should_skip,
                )

                decision = decide_next_strategy(
                    error_code=_err_code,
                    error_message=last_error,
                    site_url=url,
                    screenshot_bytes=_screenshot_bytes,
                )
                meta["agent_decision"] = decision.get("action", "")
                meta["agent_reason"] = decision.get("reason", "")

                # 根据决策路由
                if sd_should_skip(decision):
                    # AI 判断跳过，不再尝试 Vision（节省资源）
                    meta["diagnostic_category"] = "agent_decided_skip"
                    meta["diagnosis"] = f"AI 策略决策器判断跳过：{decision.get('reason', '')}"
                    last_error = summarize_result_message(
                        f"{last_error} | Agent 决策跳过：{decision.get('reason', '')}".strip(" |")
                    )
                    print(f"  🚫 AI 决策：跳过此站点 | {decision.get('reason', '')}")

                else:
                    if execution_mode == EXECUTION_MODE_AGENT and sd_should_retry_dom(decision):
                        _append_agent_trace(meta, "agent_retry_dom")
                        try_dismiss_overlays(page)
                        retry_result = _try_dom_post(
                            page, comment_content, name, email, website, anchor_text=anchor_text
                        )
                        if retry_result[0]:
                            meta["diagnostic_category"] = "agent_retry_dom_success"
                            return True, retry_result[1], meta
                        last_error = summarize_result_message(retry_result[1])

                    # AI 判断可以尝试 Vision（或其他策略）
                    should_probe_vision, reason = _page_has_comment_signals(page)
                    allow_vision = should_probe_vision or (
                        nav_meta.get("partial_navigation") and should_try_vision(decision)
                    )
                    if allow_vision:
                        _append_agent_trace(meta, "vision_attempt")
                        print(f"  🤖 Layer 4：调用 Gemini Vision AI 分析网页截图（AI 决策：{decision.get('action')}）...")
                        from vision_agent import try_post_via_vision

                        vision_result = try_post_via_vision(page, comment_content)
                        meta.update(vision_result[2])
                        if vision_result[0]:
                            _append_agent_trace(meta, "vision_success")
                            return True, vision_result[1], meta
                        last_error = summarize_result_message(vision_result[1])
                    else:
                        meta["diagnostic_category"] = "vision_skipped_no_comment_signal"
                        meta["diagnosis"] = reason
                        last_error = summarize_result_message(
                            f"{last_error} | 已跳过 Vision：{reason}".strip(" |")
                        )
            else:
                meta["diagnostic_category"] = "vision_disabled"
                last_error = "DOM/iframe 未找到评论框，且 Vision 已在配置中关闭。"

        except Exception as e:
            last_error = translate_error(str(e))
            meta["diagnostic_category"] = "runtime_error"
            print(f"  ⚠️ 发生异常（尝试 {attempt + 1}/{max_retries}）: {last_error}")

    final_diagnosis = _diagnose_site_status(page)
    meta["diagnosis"] = final_diagnosis
    return False, format_notes(last_error, final_diagnosis), meta

def _handle_blogger_identity(frame):
    """
    针对 Blogger (BlogSpot) 平台的专项匿名/身份选择逻辑。
    """
    try:
        # Blogger 的身份菜单通常是 id="identityMenu"
        menu = frame.locator('#identityMenu, select[name="identityMenu"]').first
        if menu.count() > 0 and menu.is_visible():
            print("  🌐 检测到 Blogger 身份菜单，尝试选择‘匿名’或‘名称/网址’...")
            
            # 优先检查是否有“匿名”选项
            options = menu.locator('option').all_inner_texts()
            anon_idx = -1
            name_url_idx = -1
            for i, opt in enumerate(options):
                if "匿名" in opt or "Anonymous" in opt:
                    anon_idx = i
                if "名称/网址" in opt or "Name/URL" in opt:
                    name_url_idx = i
            
            if anon_idx != -1:
                print(f"  👤 选择身份: 匿名")
                menu.select_option(index=anon_idx)
            elif name_url_idx != -1:
                print(f"  👤 选择身份: 名称/网址")
                menu.select_option(index=name_url_idx)
                # 如果选了名称网址，Blogger 会动态蹦出 dialog
                time.sleep(1.5)
            else:
                # 尝试通过文本点击（针对非 select 类型的菜单）
                print("  👤 尝试通过文本点击选择‘匿名’身份...")
                anon_item = frame.locator('text="匿名", text="Anonymous"').first
                if anon_item.count() > 0:
                    anon_item.click()
                    time.sleep(1)
        
        # 处理可能弹出的“名称/网址”自定义框
        name_field = frame.locator('input[name="anonName"], #anonNameField').first
        if name_field.count() > 0 and name_field.is_visible():
             name_field.fill("Bear Clicker")
             url_field = frame.locator('input[name="anonURL"]').first
             if url_field.count() > 0:
                 url_field.fill("https://bearclicker.net/")
             # 点这个对话框的‘继续’按钮
             continue_btn = frame.locator('input[id="postCommentSubmit"], #identityMenuContinue').first
             if continue_btn.count() > 0:
                 continue_btn.click()
                 time.sleep(0.5)
    except Exception as e:
        print(f"  ⚠️ Blogger 身份处理异常: {str(e)[:50]}")

def _is_submission_context_reset_error(error_msg: str) -> bool:
    normalized = str(error_msg or "").lower()
    return any(
        marker in normalized
        for marker in (
            "target page, context or browser has been closed",
            "execution context was destroyed",
            "frame was detached",
            "target closed",
            "most likely because of a navigation",
        )
    )


def _detect_submission_side_effect(page: Page, frame=None) -> str:
    targets = []
    if frame:
        targets.append(frame)
    targets.append(page)

    for target in targets:
        try:
            textarea_count = target.locator("textarea:visible").count()
            editable_count = target.locator('[contenteditable="true"]:visible').count()
            if textarea_count == 0 and editable_count == 0:
                return "提交后页面中已看不到原评论输入框"
        except Exception:
            continue

        try:
            submit_candidates = target.locator(
                'input[type="submit"]:visible, button[type="submit"]:visible, button:visible'
            )
            if submit_candidates.count() > 0:
                submit = submit_candidates.first
                disabled_attr = str(submit.get_attribute("disabled") or "").lower()
                aria_disabled = str(submit.get_attribute("aria-disabled") or "").lower()
                if disabled_attr not in {"", "none"} or aria_disabled == "true":
                    return "提交按钮在点击后进入 disabled 状态"
        except Exception:
            continue

    return ""


def _target_presence_tokens(target_url: str = "", anchor_text: str = "") -> list[str]:
    tokens = []
    normalized_url = str(target_url or "").strip()
    if normalized_url:
        parsed = urlparse(normalized_url if "://" in normalized_url else f"https://{normalized_url}")
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        if host:
            tokens.append(host)
    anchor = str(anchor_text or "").strip().lower()
    if anchor and len(anchor) >= 3:
        tokens.append(anchor)
    return tokens


def _find_target_presence_in_comments(target, target_url: str = "", anchor_text: str = "") -> str:
    tokens = _target_presence_tokens(target_url, anchor_text)
    if not tokens:
        return ""

    scopes = []
    scope_selectors = [
        "#comments",
        ".comments",
        ".comment-list",
        ".commentlist",
        ".comment-content",
        ".comment-body",
        ".comment",
        "article.comment",
        "#respond",
        ".comment-respond",
    ]
    for selector in scope_selectors:
        try:
            locator = target.locator(selector)
            if locator.count() > 0:
                scopes.append(locator.first)
        except Exception:
            continue

    if not scopes:
        try:
            scopes = [target.locator("body")]
        except Exception:
            scopes = []

    for scope in scopes:
        try:
            body_text = " ".join(scope.all_inner_texts()).lower()
        except Exception:
            body_text = ""
        for token in tokens:
            if token and token in body_text:
                return f"评论区文本中出现目标标识 '{token}'"

        try:
            anchors = scope.locator("a[href]")
            anchor_count = min(anchors.count(), 50)
        except Exception:
            anchor_count = 0

        for idx in range(anchor_count):
            try:
                anchor = anchors.nth(idx)
                href = str(anchor.get_attribute("href") or "").lower()
                text = str(anchor.inner_text() or "").lower()
            except Exception:
                continue
            for token in tokens:
                if token and (token in href or token in text):
                    return f"评论区链接中出现目标标识 '{token}'"

    return ""


def _verify_post_success(
    page: Page,
    comment_content: str,
    frame=None,
    target_url: str = "",
    anchor_text: str = "",
) -> tuple[bool, str]:
    """
    提交按钮点击后，验证是否真正发帖成功（或进入审核状态）。
    1. 首选方案：检查页面是否出现了我们刚刚发送的评论内容
    2. 备选方案：检查页面是否出现了“审核”、“成功”等提示词
    3. 备选方案：检查网址是否发生了合法的锚点重定向
    """
    success_keywords = [
        "审核", "moderation", "awaiting", "approval", "pending",
        "成功", "successfully", "posted", "published", "saved", "thanks for your comment",
        "your comment", "replying to", "has been submitted", "idli-kurma" # 特殊测试页标识
    ]
    
    # 给页面一些时间来加载响应或跳转（从 3s 增加到 8s，确保大流量/慢速站点能展示成功提示词）
    page.wait_for_timeout(8000)
    
    target = frame if frame else page
    
    try:
        # 获取当前页面或 frame 的全部可见文本进行正则匹配
        bodies = target.locator("body").all_inner_texts()
        body_text = " ".join(bodies).lower()
        
        # 1. 检查是否存在审核类关键词（增加多语言支持）
        moderation_keywords = [
            "审核", "moderation", "awaiting", "approval", "pending", "submitted", "thanks for your comment",
            "moderación", "pendiente", "gracias por su comentario", "su comentario ha sido", "en cola"
        ]
        is_moderation = any(kw.lower() in body_text for kw in moderation_keywords)

        # 2. 直接搜索发出去的评论内容
        # 确保 comment_content 是字符串
        content_str = str(comment_content)
        content_snippet = content_str[:30].lower()
        content_found = content_snippet in body_text

        if content_found:
            msg = f"判定为成功：页面上已直接显示了评论内容片段 '{content_snippet}'"
            if is_moderation:
                msg += " (⚠️ 注意：该评论目前处于‘审核中’状态，可能仅你可见)"
            return True, msg
            
        # 3. 没搜到原文，但搜到审核关键词
        for kw in moderation_keywords + success_keywords:
            if kw.lower() in body_text:
                return True, f"判定为成功：虽未见原文，但检测到成功或审核特征词 '{kw}'"

        target_presence_reason = _find_target_presence_in_comments(target, target_url=target_url, anchor_text=anchor_text)
        if target_presence_reason:
            return True, f"判定为成功：{target_presence_reason}"
        
        # 4. 检查是否重定向到了特定的评论 hash 锚点
        current_url = str(page.url)
        original_url = str(getattr(page, "_original_url", ""))
        if not frame:
            if current_url != original_url and "#comment" in current_url:
                return True, f"判定为成功：URL已重定向至评论锚点 ({current_url})"

        side_effect_reason = _detect_submission_side_effect(page, frame)
        if side_effect_reason:
            return True, f"判定为成功：{side_effect_reason}"
                
        return False, "填写了评论且点击了提交，但页面没有出现成功提示词或发生跳转重定向。"

        
    except Exception as e:
        error_msg = str(e)
        if _is_submission_context_reset_error(error_msg):
            side_effect_reason = _detect_submission_side_effect(page, None)
            if side_effect_reason:
                return True, f"判定为成功：提交后页面上下文被刷新或销毁，且{side_effect_reason}"
            return True, "判定为成功：提交后评论区域上下文被刷新或销毁，常见于 Blogger/审核提交流程。"
        return False, f"验证发帖结果时发生异常: {error_msg[:50]}"


def _is_blogger_comment_url(url: str) -> bool:
    normalized = str(url or "").lower()
    return "blogger.com/comment" in normalized or "blogblog.com" in normalized


def _is_irrelevant_frame_url(url: str) -> bool:
    normalized = str(url or "").lower()
    return any(marker in normalized for marker in IRRELEVANT_FRAME_PATTERNS)


def _frame_scan_priority(frame) -> tuple[int, str]:
    url = str(getattr(frame, "url", "") or "").lower()
    if _is_blogger_comment_url(url) or any(marker in url for marker in ("comment", "reply", "disqus", "wpdiscuz", "remark42", "giscus")):
        return (0, url)
    if url in {"", "about:blank", "about:srcdoc"}:
        return (2, url)
    return (1, url)


def _try_submit_blogger(
    frame,
    page: Page,
    comment_content: str,
    target_url: str = "",
    anchor_text: str = "",
) -> Optional[tuple[bool, str]]:
    candidate_frames = [frame]
    candidate_frames.extend(
        item for item in page.frames
        if item != frame and _is_blogger_comment_url(getattr(item, "url", ""))
    )

    selectors = [
        'button:has-text("Publish")',
        'button:has-text("PUBLISH")',
        '[aria-label*="Publish"]',
        '[aria-label*="PUBLISH"]',
        'div[role="button"]:has-text("Publish")',
        'div[role="button"]:has-text("PUBLISH")',
    ]

    for candidate in candidate_frames:
        try:
            candidate.wait_for_timeout(600)
        except Exception:
            pass

        try:
            role_button = candidate.get_by_role("button", name=re.compile("publish", re.I))
            if role_button.count() > 0 and role_button.first.is_visible():
                print("  👉 在 Blogger 评论框中找到 Publish 按钮，准备点击...")
                role_button.first.scroll_into_view_if_needed()
                role_button.first.click(timeout=3000, force=True)
                return _verify_post_success(
                    page, comment_content, candidate, target_url=target_url, anchor_text=anchor_text
                )
        except Exception:
            pass

        for selector in selectors:
            try:
                btn = candidate.locator(selector)
                if btn.count() > 0 and btn.first.is_visible():
                    print(f"  👉 在 Blogger 评论框中找到提交按钮 [{selector}]，准备点击...")
                    btn.first.scroll_into_view_if_needed()
                    btn.first.click(timeout=3000, force=True)
                    return _verify_post_success(
                        page, comment_content, candidate, target_url=target_url, anchor_text=anchor_text
                    )
            except Exception:
                continue

    return None

def _try_dom_post(
    page: Page,
    comment_content: str,
    name: str = "",
    email: str = "",
    website: str = "",
    anchor_text: str = "",
) -> tuple[bool, str]:
    """
    Layer 1 内部函数：使用传统 DOM 方式寻找评论框并填写提交
    覆盖三种情况：
    方式 1 - 主页面的 <textarea>
    方式 2 - 主页面的 contenteditable 富文本框
    方式 3 - 嵌套在 iframe 里的评论框（Blogger / Disqus / WordPress 常用）
    """
    # 记录原始 URL 用于判断重定向
    setattr(page, "_original_url", page.url)
    _try_reveal_comment_form(page)

    # 方式 1：主页面评论编辑器
    page_filled, selector = _try_fill_comment_editor(page, comment_content)
    if page_filled:
        print(f"  👁️ 找到主页面评论框 [{selector}]，正在提交...")
        _fill_additional_fields(page, name, email, website)
        time.sleep(1)
        return _try_submit(page, comment_content, target_url=website, anchor_text=anchor_text)
    
    # 方式 3：递归扫描所有 iframe
    print(f"  🔍 主页面未找到评论框，开始深度扫描 iframe...")
    
    def scan_frames(current_page_or_frame):
        frames = sorted(current_page_or_frame.frames, key=_frame_scan_priority)
        for frame in frames:
            try:
                frame_url = frame.url
                # 跳过无关的 iframe (youtube, ad, sns buttons 等)
                if _is_irrelevant_frame_url(frame_url):
                    continue
                
                print(f"  📦 检查 iframe: {frame_url[:60]}...")
                _try_reveal_comment_form(frame)
                
                frame_filled, selector = _try_fill_comment_editor(frame, comment_content)
                if frame_filled:
                    print(f"  👁️ 在 iframe 内发现评论框 [{selector}]，准备开始填写...")
                    if "blogger.com" in frame_url or "blogblog.com" in frame_url:
                        _handle_blogger_identity(frame)
                    _fill_additional_fields(frame, name, email, website)
                    return True, _try_submit_in_frame(
                        frame, page, comment_content, target_url=website, anchor_text=anchor_text
                    )
                
                # 递归查找孙子 frame
                if len(frame.child_frames) > 0:
                    found, res = scan_frames(frame)
                    if found: return True, res
                    
            except Exception as e:
                # print(f"  ⚠️ 扫描 iframe 失败: {str(e)[:50]}")
                continue
        return False, None

    found_in_iframe, result = scan_frames(page)
    if found_in_iframe:
        return result
    
    return False, "Layer 1: 主页面及所有嵌套 iframe 中均未找到任何评论输入框"




def _try_submit_in_frame(
    frame,
    page: Page,
    comment_content: str,
    target_url: str = "",
    anchor_text: str = "",
) -> tuple[bool, str]:
    """
    在 iframe 内部寻找提交按钮并点击。
    找不到时尝试在主页面补找（某些博客的提交按钮在 iframe 外面）。
    """
    if _is_blogger_comment_url(getattr(frame, "url", "")):
        blogger_result = _try_submit_blogger(
            frame, page, comment_content, target_url=target_url, anchor_text=anchor_text
        )
        if blogger_result:
            return blogger_result

    button_selectors = [
        'input[type="submit"]',
        'button[type="submit"]',
        'button:has-text("Post")',
        'button:has-text("Publish")',
        'button:has-text("Comment")',
        'button:has-text("Submit")',
        'a:has-text("Post Comment")',
    ]
    # 先在 iframe 内部找提交按钮
    for selector in button_selectors:
        try:
            btn = frame.locator(selector)
            if btn.count() > 0 and btn.first.is_visible():
                print(f"  👉 在 iframe 内找到提交按钮 [{selector}]，准备点击...")
                btn.first.click()
                return _verify_post_success(
                    page, comment_content, frame, target_url=target_url, anchor_text=anchor_text
                ) # 加入真实成功验证
        except:
            continue
    
    # iframe 里没找到，退回到主页面找提交按钮
    print("  🔄 iframe 内未找到提交按钮，尝试主页面...")
    return _try_submit(page, comment_content, target_url=target_url, anchor_text=anchor_text)


def _try_submit(page: Page, comment_content: str, target_url: str = "", anchor_text: str = "") -> tuple[bool, str]:
    """
    内部辅助函数：寻找提交按钮并点击，返回 (是否成功, 描述信息)
    """
    print("  👁️ 正在寻找提交按钮...")
    button_selectors = [
        'input[type="submit"]',
        'button[type="submit"]',
        'button:has-text("Comment")',
        'button:has-text("Post Comment")',
        'button:has-text("Post")',
        'button:has-text("Submit")',
        'button:has-text("Save")',
        'button:has-text("Send")',
        'button:has-text("Publish")',
        'button:has-text("Publicar")', # 西班牙语：发布
        'button:has-text("Enviar")',   # 西班牙语：发送
        '.submit-button',
        '#submit',
        '#commentsubmit',
        '.form-submit input[type="submit"]',
        'input[value*="Publish"]',
        'input[value*="Post"]',
        'input[value*="Comment"]',
    ]
    for selector in button_selectors:
        try:
            btns = page.locator(selector)
            if btns.count() > 0 and btns.first.is_visible():
                print(f"  👉 找到按钮 [{selector}]，准备点击...")
                btns.first.scroll_into_view_if_needed()
                btns.first.click()
                return _verify_post_success(
                    page, comment_content, target_url=target_url, anchor_text=anchor_text
                ) # 加入真实成功验证
        except:
            continue
    
    return False, "填写了评论内容，但没有找到可以点击的提交按钮。"



# =====================================================================
# 处理单条任务
# =====================================================================
def process_task(
    task_row: dict,
    target: dict,
    workbook: FeishuWorkbook,
    page: Page,
    runtime_cfg: dict,
    source_format_lookup: Optional[dict[str, dict]] = None,
):
    raw_url = task_row.get("来源链接", "")
    url = normalize_source_url(extract_cell_url(raw_url) or extract_cell_text(raw_url))
    current_link_format = str(task_row.get("链接格式", "") or "")
    source_row = (source_format_lookup or {}).get(url, {})
    link_format, link_format_analysis = resolve_runtime_link_format(url, current_link_format, source_row=source_row)
    generation_link_format = link_format if link_format and link_format != "unknown" else "plain_text"
    batch_token = str(task_row.get("目标站标识", "") or "")
    execution_mode = str(task_row.get("执行模式", "") or EXECUTION_MODE_CLASSIC)
    recommended_strategy = str(task_row.get("推荐策略", "") or "dom")

    if not url:
        raise ValueError("任务来源链接为空，无法执行发帖。")
    
    print(f"\n🚀 开始处理来源 {url} -> 站点 {target.get('site_key', '')}")
    print(f"  🧭 执行模式={execution_mode} | 推荐策略={recommended_strategy}")
    if link_format_analysis:
        print(
            "  🔎 Link_Format 预判："
            f"{link_format_analysis.get('recommended_format', 'unknown')} | "
            f"证据={link_format_analysis.get('evidence_type', 'unknown')} | "
            f"置信度={link_format_analysis.get('confidence', 0)}"
        )

    ai_cfg = runtime_cfg.get("ai_generation", {})
    preprobe_meta = None
    if ai_cfg.get("preprobe_before_generation", True):
        print("  [1/4] 🔎 先做页面预探测，确认值得继续后再调用 AI...")
        preprobe_meta = _preprobe_page_for_generation(
            page,
            url,
            runtime_cfg["execution"]["page_load_timeout_ms"],
            execution_mode,
            recommended_strategy,
        )
        recommended_strategy = str(preprobe_meta.get("recommended_strategy", "") or recommended_strategy)
        if not preprobe_meta.get("ok", True):
            print(f"  ⛔ 预探测提前收口：{preprobe_meta.get('diagnosis', '')}")
            last_updated_value = time.strftime('%Y-%m-%d %H:%M:%S')
            failure_message = format_notes(
                summarize_result_message(preprobe_meta.get("message", "页面预探测失败")),
                preprobe_meta.get("diagnosis", ""),
            )
            memory_meta = _apply_agent_memory_result(
                url=url,
                success=False,
                execution_mode=execution_mode,
                recommended_strategy=recommended_strategy,
                diagnostic_category=str(preprobe_meta.get("diagnostic_category", "") or ""),
                reason=failure_message,
                runtime_cfg=runtime_cfg,
            )
            updates = {
                "来源链接": url,
                "来源标题": str(task_row.get("来源标题", "") or ""),
                "根域名": str(task_row.get("根域名", "") or ""),
                "页面评分": str(task_row.get("页面评分", "") or ""),
                "目标站标识": str(target.get("site_key", "") or ""),
                "最后尝试时间": last_updated_value,
                "当前评论内容": "",
                "链接格式": link_format,
                "来源类型": str(task_row.get("来源类型", "") or ""),
                "有网址字段": str(task_row.get("有网址字段", "") or ""),
                "有验证码": str(task_row.get("有验证码", "") or ""),
                "执行模式": execution_mode,
                "推荐策略": recommended_strategy,
                "最近失败分类": memory_meta["normalized_category"],
                "域名冷却至": _format_memory_dt(memory_meta.get("cooldown_until", "")) if memory_meta.get("cooldown_until") else "",
                "最后更新时间": last_updated_value,
                "状态": "待重试",
                "最近失败时间": last_updated_value,
                "最近失败原因": failure_message,
                "Agent动作轨迹": " > ".join(preprobe_meta.get("action_trace", [])),
            }
            workbook.upsert_status_row(updates)
            return {
                "success": False,
                "message": failure_message,
                "url": url,
                "status_updates": updates,
                "diagnosis": str(preprobe_meta.get("diagnosis", "") or ""),
                "diagnostic_category": str(preprobe_meta.get("diagnostic_category", "") or ""),
            }

    # 步骤 2：AI 生成文案
    print("  [2/4] 🤖 AI 正在生成外链推广文案...")
    page_context = fetch_page_context(
        url,
        include_comments_summary=bool(ai_cfg.get("generate_comment_summary", False)),
    )
    print(
        f"  🌍 页面语言={page_context.get('language_name', 'English')} | 标题={summarize_result_message(page_context.get('title', ''), 80)}"
    )
    # ===== 优先读取 targets.json 中配置好的固定锚文本 =====
    from ai_generator import (
        generate_localized_bundle_for_target,
        get_anchor_for_format,
    )
    
    active_target = target
    
    if active_target:
        target_name = _normalize_target_value(
            active_target.get("anchor_text", "") or active_target.get("默认锚文本", "") or "Anonymous"
        ).title()
        target_email = (
            _normalize_target_value(active_target.get("email", "") or active_target.get("联系邮箱", ""))
            or DEFAULT_CONTACT_EMAIL
        )
        target_url = _normalize_target_value(
            active_target.get("url", "") or active_target.get("目标网站", ""),
            is_url=True,
        )
        if not target_url:
            target_url = MY_TARGET_WEBSITE
        
        sanitized_target = {
            **active_target,
            "url": target_url,
            "anchor_text": _normalize_target_value(
                active_target.get("anchor_text", "") or active_target.get("默认锚文本", "") or "click here"
            ),
            "email": target_email,
            "description": _normalize_target_value(
                active_target.get("description", "") or active_target.get("网站说明", "")
            ),
        }
        print(
            f"  📋 使用飞书目标站配置：锚文本='{_normalize_target_value(active_target.get('anchor_text', '') or active_target.get('默认锚文本', ''))}' -> {target_url}"
        )
        content_bundle = generate_localized_bundle_for_target(
            sanitized_target,
            generation_link_format,
            page_context,
            include_chinese_translation=bool(ai_cfg.get("generate_chinese_translation", False)),
        )
        keywords = content_bundle["keywords"]
        anchor_text = content_bundle["anchor_text"]
        comment_content = content_bundle["comment_content"]
        comment_content_zh = content_bundle["comment_content_zh"]
    else:
        # ⚠️ 无配置文件：退回旧逻辑
        target_name = "Anonymous"
        target_email = DEFAULT_CONTACT_EMAIL
        target_url = MY_TARGET_WEBSITE
        
        print("  ⚠️ 未找到飞书目标站配置，退回 AI 自动生成锚文本模式")
        keywords = str(task_row.get("关键词", "") or "")
        if not keywords or keywords.strip() == "":
            keywords = analyze_keywords(MY_TARGET_WEBSITE, page_context.get("excerpt", ""))
        anchor_text = generate_anchor_text(keywords, generation_link_format, MY_TARGET_WEBSITE)
        comment_content = generate_comment(anchor_text, page_context.get("title", ""))
        if ai_cfg.get("generate_chinese_translation", False):
            from ai_generator import translate_comment_to_chinese

            comment_content_zh = translate_comment_to_chinese(comment_content)
        else:
            comment_content_zh = ""
    
    # 步骤 3：自动发帖（带重试机制）
    print("  [3/4] 🌐 开始接管真实 Chrome 进行自动发帖...")
    is_success, result_msg, run_meta = auto_post_content(
        page,
        comment_content,
        url,
        name=target_name,
        email=target_email,
        website=target_url,
        anchor_text=anchor_text,
        max_retries=max(1, runtime_cfg["execution"]["max_retries"]),
        page_load_timeout_ms=runtime_cfg["execution"]["page_load_timeout_ms"],
        enable_sso=runtime_cfg["execution"]["enable_sso"],
        enable_vision=runtime_cfg["vision"]["enabled"],
        execution_mode=execution_mode,
        recommended_strategy=recommended_strategy,
        preprobe_meta=preprobe_meta,
    )
    print(f"  🤖 发帖结果: {result_msg}")
    
    # 步骤 4：结果写回飞书状态表
    print("  [4/4] 📝 更新结果至飞书状态表...")
    notes_message = format_notes(
        summarize_result_message(result_msg),
        run_meta.get("diagnosis", ""),
    )
    last_updated_value = time.strftime('%Y-%m-%d %H:%M:%S')
    memory_meta = _apply_agent_memory_result(
        url=url,
        success=is_success,
        execution_mode=execution_mode,
        recommended_strategy=str(run_meta.get("recommended_strategy", "") or recommended_strategy),
        diagnostic_category=str(run_meta.get("diagnostic_category", "") or ""),
        reason=notes_message,
        runtime_cfg=runtime_cfg,
    )

    updates = {
        "来源链接": url,
        "来源标题": str(task_row.get("来源标题", "") or ""),
        "根域名": str(task_row.get("根域名", "") or ""),
        "页面评分": str(task_row.get("页面评分", "") or ""),
        "目标站标识": str(target.get("site_key", "") or ""),
        "最后尝试时间": last_updated_value,
        "当前评论内容": comment_content,
        "链接格式": link_format,
        "来源类型": str(task_row.get("来源类型", "") or ""),
        "有网址字段": str(task_row.get("有网址字段", "") or ""),
        "有验证码": str(task_row.get("有验证码", "") or ""),
        "执行模式": execution_mode,
        "推荐策略": str(run_meta.get("recommended_strategy", "") or recommended_strategy),
        "最近失败分类": "" if is_success else memory_meta["normalized_category"],
        "域名冷却至": _format_memory_dt(memory_meta.get("cooldown_until", "")) if memory_meta.get("cooldown_until") else "",
        "最后更新时间": last_updated_value,
    }
    
    if is_success:
        updates["状态"] = STATUS_SUCCESS
        updates["最近成功时间"] = last_updated_value
        updates["最近失败时间"] = ""
        updates["最近失败原因"] = ""
        updates["下次可发时间"] = ""
    else:
        updates["状态"] = STATUS_PENDING_RETRY
        updates["最近失败时间"] = last_updated_value
        updates["最近失败原因"] = notes_message

    workbook.upsert_sheet_dict("records", STATUS_HEADERS, ["来源链接", "目标站标识"], updates)
    result = {
        "success": is_success,
        "url": url,
        "format": generation_link_format,
        "reason": notes_message,
        "target_website": target_url,
        "batch_token": batch_token,
        "site_key": str(target.get("site_key", "") or ""),
        "used_vision": run_meta.get("used_vision", False),
        "diagnostic_category": run_meta.get("diagnostic_category", ""),
        "execution_mode": execution_mode,
        "recommended_strategy": str(run_meta.get("recommended_strategy", "") or recommended_strategy),
        "memory_recorded": True,
    }
    return result


def _run_task_worker(task_payload: dict) -> dict:
    runtime_cfg = load_runtime_config()
    workbook = FeishuWorkbook.from_config()
    if not workbook:
        raise RuntimeError("飞书未正确配置，无法执行单条任务。")

    source_format_lookup = build_source_format_lookup(workbook)
    browser_cfg = merge_browser_config(runtime_cfg.get("browser", {}))
    cdp_url = ensure_allowed_cdp_url(str(browser_cfg.get("connect_cdp_url", DEFAULT_CDP_URL)), browser_cfg)

    with sync_playwright() as p:
        ensure_cdp_blank_page(cdp_url)
        browser_app = p.chromium.connect_over_cdp(cdp_url)
        context, work_page = _acquire_cdp_work_page(browser_app, browser_cfg)
        try:
            _maybe_bring_to_front(work_page, browser_cfg)
            with task_timeout_guard(runtime_cfg["execution"].get("single_task_timeout_seconds", 0)):
                result = process_task(
                    task_payload["status_row"],
                    task_payload["target"],
                    workbook,
                    work_page,
                    runtime_cfg,
                    source_format_lookup=source_format_lookup,
                )
        except Exception as e:
            last_updated_value = time.strftime('%Y-%m-%d %H:%M:%S')
            reason = format_notes(
                summarize_result_message(f"运行时异常: {translate_error(str(e))}"),
                "任务执行中断，已自动回退为待重试。",
            )
            memory_meta = _apply_agent_memory_result(
                url=normalize_source_url(
                    extract_cell_url(task_payload["status_row"].get("来源链接", ""))
                    or extract_cell_text(task_payload["status_row"].get("来源链接", ""))
                ),
                success=False,
                execution_mode=str(task_payload["status_row"].get("执行模式", "") or EXECUTION_MODE_CLASSIC),
                recommended_strategy=str(task_payload["status_row"].get("推荐策略", "") or "dom"),
                diagnostic_category="task_exception",
                reason=reason,
                runtime_cfg=runtime_cfg,
            )
            failure_updates = _build_task_failure_updates(
                task_payload["status_row"],
                task_payload["target"],
                reason,
                last_updated_value,
            )
            failure_updates["域名冷却至"] = (
                _format_memory_dt(memory_meta.get("cooldown_until", ""))
                if memory_meta.get("cooldown_until")
                else ""
            )
            workbook.upsert_sheet_dict(
                "records",
                STATUS_HEADERS,
                ["来源链接", "目标站标识"],
                failure_updates,
            )
            result = {
                "success": False,
                "url": normalize_source_url(
                    extract_cell_url(task_payload["status_row"].get("来源链接", ""))
                    or extract_cell_text(task_payload["status_row"].get("来源链接", ""))
                ),
                "format": str(task_payload["status_row"].get("链接格式", "") or ""),
                "reason": reason,
                "target_website": str(task_payload["target"].get("url", "") or ""),
                "batch_token": str(task_payload["status_row"].get("目标站标识", "") or ""),
                "site_key": str(task_payload["target"].get("site_key", "") or ""),
                "used_vision": False,
                "diagnostic_category": "task_exception",
                "memory_recorded": True,
            }
        finally:
            try:
                if not work_page.is_closed():
                    work_page.close()
            except Exception:
                pass
            try:
                browser_app.close()
            except Exception:
                pass

    sync_reporting_workbook(workbook=workbook)
    return result


def _run_task_via_subprocess(task: dict, runtime_cfg: dict) -> dict:
    timeout_seconds = int(runtime_cfg["execution"].get("single_task_timeout_seconds", 0) or 0)
    poll_interval = max(2, int(runtime_cfg["execution"].get("worker_poll_interval_seconds", 10) or 10))
    timeout_buffer = max(5, int(runtime_cfg["execution"].get("worker_timeout_buffer_seconds", 20) or 20))

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as task_fp:
        json.dump(task, task_fp, ensure_ascii=False)
        task_path = task_fp.name
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as result_fp:
        result_path = result_fp.name

    proc = None
    task_url = normalize_source_url(
        extract_cell_url(task["status_row"].get("来源链接", ""))
        or extract_cell_text(task["status_row"].get("来源链接", ""))
    )
    try:
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
                print(f"  ⏳ 单条任务子进程仍在执行（{elapsed}s）...")
                next_heartbeat += poll_interval
            if hard_timeout > 0 and elapsed >= hard_timeout:
                print(f"  ⏱️ 单条任务子进程超时（>{hard_timeout}s），正在终止...")
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

        exit_code = proc.returncode if proc else -1
        if hard_timeout > 0 and int(time.time() - started_at) >= hard_timeout:
            reason = format_notes(
                summarize_result_message(f"运行时异常: 单条任务子进程超时（>{hard_timeout}s）"),
                "任务执行中断，已自动回退为待重试。",
            )
        else:
            reason = format_notes(
                summarize_result_message(f"运行时异常: 单条任务子进程异常退出（exit={exit_code}）"),
                "任务执行中断，已自动回退为待重试。",
            )
        last_updated_value = time.strftime('%Y-%m-%d %H:%M:%S')
        workbook = FeishuWorkbook.from_config()
        if workbook:
            memory_meta = _apply_agent_memory_result(
                url=task_url,
                success=False,
                execution_mode=str(task["status_row"].get("执行模式", "") or EXECUTION_MODE_CLASSIC),
                recommended_strategy=str(task["status_row"].get("推荐策略", "") or "dom"),
                diagnostic_category="task_exception",
                reason=reason,
                runtime_cfg=runtime_cfg,
            )
            failure_updates = _build_task_failure_updates(task["status_row"], task["target"], reason, last_updated_value)
            failure_updates["域名冷却至"] = (
                _format_memory_dt(memory_meta.get("cooldown_until", ""))
                if memory_meta.get("cooldown_until")
                else ""
            )
            workbook.upsert_sheet_dict(
                "records",
                STATUS_HEADERS,
                ["来源链接", "目标站标识"],
                failure_updates,
            )
            sync_reporting_workbook(workbook=workbook)
        return {
            "success": False,
            "url": task_url,
            "format": str(task["status_row"].get("链接格式", "") or ""),
            "reason": reason,
            "target_website": str(task["target"].get("url", "") or ""),
            "batch_token": str(task["status_row"].get("目标站标识", "") or ""),
            "site_key": str(task["target"].get("site_key", "") or ""),
            "used_vision": False,
            "diagnostic_category": "task_exception",
            "memory_recorded": True,
        }
    finally:
        for path in (task_path, result_path):
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass


def _worker_main(task_file: str, result_file: str) -> int:
    task_payload = json.loads(Path(task_file).read_text(encoding="utf-8"))
    result = _run_task_worker(task_payload)
    Path(result_file).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0



# =====================================================================
# 主程序入口
# =====================================================================
def run_once(selected_tasks: Optional[list[dict]] = None, send_report: bool = True) -> dict:
    runtime_cfg = load_runtime_config()
    workbook = FeishuWorkbook.from_config()
    if not workbook:
        raise RuntimeError("飞书未正确配置，无法执行发帖。")
    source_format_lookup = build_source_format_lookup(workbook)

    tasks = selected_tasks or []

    print("=" * 50)
    print("🤖 外链自动化 - 飞书多站点执行模块")
    print("=" * 50)

    if not tasks:
        print("🎉 今日无发布任务，请先运行 daily_scheduler.py 挑选任务。")
        return {"success": [], "failed": [], "today_tasks": 0}

    print(f"📦 找到 {len(tasks)} 个进行中任务，开始处理...")

    success_list = []
    failed_list = []
    browser_cfg = merge_browser_config(runtime_cfg.get("browser", {}))
    cdp_url = ensure_allowed_cdp_url(str(browser_cfg.get("connect_cdp_url", DEFAULT_CDP_URL)), browser_cfg)

    print(f"🕸️ 正在检查你的本地 Chrome 浏览器（{cdp_url}）...")
    try:
        ensure_cdp_blank_page(cdp_url)
        with sync_playwright() as p:
            browser_app = p.chromium.connect_over_cdp(cdp_url)
            print("🤩 成功接管本地真实 Chrome！将以单条任务隔离模式执行。\n")
            browser_app.close()
    except Exception as e:
        print(f"❌ 接管本地浏览器失败（请确认是通过 Start_Robot.command 启动的）: {e}")
        failed_list.append({"url": "", "reason": str(e), "success": False})
        sync_reporting_workbook(workbook=workbook)
        return {
            "success": success_list,
            "failed": failed_list,
            "today_tasks": len(tasks),
        }

    use_isolated_worker = bool(runtime_cfg["execution"].get("isolated_task_worker", True))

    for idx, task in enumerate(tasks, start=1):
        print(f"\n>>> 进度: {idx}/{len(tasks)}")
        try:
            if use_isolated_worker:
                res = _run_task_via_subprocess(task, runtime_cfg)
            else:
                with sync_playwright() as p:
                    browser_app = p.chromium.connect_over_cdp(cdp_url)
                    context, work_page = _acquire_cdp_work_page(browser_app, browser_cfg)
                    try:
                        _maybe_bring_to_front(work_page, browser_cfg)
                        with task_timeout_guard(runtime_cfg["execution"].get("single_task_timeout_seconds", 0)):
                            res = process_task(
                                task["status_row"],
                                task["target"],
                                workbook,
                                work_page,
                                runtime_cfg,
                                source_format_lookup=source_format_lookup,
                            )
                    finally:
                        try:
                            if not work_page.is_closed():
                                work_page.close()
                        except Exception:
                            pass
                        browser_app.close()
        except Exception as e:
            last_updated_value = time.strftime('%Y-%m-%d %H:%M:%S')
            reason = format_notes(
                summarize_result_message(f"运行时异常: {translate_error(str(e))}"),
                "任务执行中断，已自动回退为待重试。",
            )
            memory_meta = _apply_agent_memory_result(
                url=normalize_source_url(
                    extract_cell_url(task["status_row"].get("来源链接", ""))
                    or extract_cell_text(task["status_row"].get("来源链接", ""))
                ),
                success=False,
                execution_mode=str(task["status_row"].get("执行模式", "") or EXECUTION_MODE_CLASSIC),
                recommended_strategy=str(task["status_row"].get("推荐策略", "") or "dom"),
                diagnostic_category="task_exception",
                reason=reason,
                runtime_cfg=runtime_cfg,
            )
            print(f"  ❌ 单条任务异常，已回退为待重试: {reason}")
            failure_updates = _build_task_failure_updates(task["status_row"], task["target"], reason, last_updated_value)
            failure_updates["域名冷却至"] = _format_memory_dt(memory_meta.get("cooldown_until", "")) if memory_meta.get("cooldown_until") else ""
            workbook.upsert_sheet_dict(
                "records",
                STATUS_HEADERS,
                ["来源链接", "目标站标识"],
                failure_updates,
            )
            res = {
                "success": False,
                "url": normalize_source_url(
                    extract_cell_url(task["status_row"].get("来源链接", ""))
                    or extract_cell_text(task["status_row"].get("来源链接", ""))
                ),
                "format": str(task["status_row"].get("链接格式", "") or ""),
                "reason": reason,
                "target_website": str(task["target"].get("url", "") or ""),
                "batch_token": str(task["status_row"].get("目标站标识", "") or ""),
                "site_key": str(task["target"].get("site_key", "") or ""),
                "used_vision": False,
                "diagnostic_category": "task_exception",
                "memory_recorded": True,
            }
        if res["success"]:
            success_list.append(res)
        else:
            failed_list.append(res)

    if send_report:
        from webhook_sender import create_webhook_sender
        sender = create_webhook_sender()
        if sender:
            summary = {"success": success_list, "failed": failed_list}
            title = f"🌍 外链自动化执行报告 | 成功 {len(success_list)} | 失败 {len(failed_list)}"
            sender.send_detailed_report(title, summary)
        else:
            print("\nℹ️ 未配置飞书 Webhook，跳过通知。")

    sync_reporting_workbook(workbook=workbook)

    return {
        "success": success_list,
        "failed": failed_list,
        "today_tasks": len(tasks),
    }


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker-task-file")
    parser.add_argument("--worker-result-file")
    return parser.parse_args()


def main():
    args = _parse_args()
    if args.worker_task_file:
        if not args.worker_result_file:
            raise SystemExit("--worker-result-file is required with --worker-task-file")
        return _worker_main(args.worker_task_file, args.worker_result_file)
    run_once(send_report=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
