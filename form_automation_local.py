from __future__ import annotations

import time
import json
import re
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, Page, Frame

from browser_cdp import DEFAULT_CDP_URL, ensure_allowed_cdp_url, merge_browser_config
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
    "#comments",
    ".comments",
    ".comment-respond",
    "#respond",
    ".comment-form",
    "#commentform",
    'iframe[src*="comment"]',
    'iframe[src*="blogger"]',
    'iframe[src*="disqus"]',
    'form[id*="comment"]',
    'form[class*="comment"]',
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
        "execution": {
            "success_goal": 10,
            "page_load_timeout_ms": 30000,
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
    merged["execution"] = {**defaults["execution"], **config.get("execution", {})}
    merged["browser"] = merge_browser_config(config.get("browser", {}) or defaults["browser"])
    merged["vision"] = {**defaults["vision"], **config.get("vision", {})}
    return merged


def summarize_result_message(message: str, limit: int = 160) -> str:
    clean = " ".join(str(message).split())
    return clean if len(clean) <= limit else f"{clean[:limit - 3]}..."


def format_notes(message: str, diagnosis: str = "") -> str:
    if diagnosis and diagnosis not in message:
        return f"{message} | 自动诊断: {diagnosis}"
    return message


def _maybe_bring_to_front(page, browser_cfg: dict) -> None:
    if not browser_cfg.get("bring_to_front", False):
        return
    try:
        page.bring_to_front()
    except Exception:
        pass


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
        raise

    try:
        page.wait_for_load_state("networkidle", timeout=min(2500, max(800, effective_timeout // 6)))
    except Exception:
        pass
    return nav_meta


def _page_has_comment_signals(page: Page) -> tuple[bool, str]:
    for selector in COMMENT_SIGNAL_SELECTORS:
        try:
            locator = page.locator(selector)
            if locator.count() > 0:
                return True, f"命中选择器 {selector}"
        except Exception:
            continue

    try:
        body_text = " ".join(page.locator("body").all_inner_texts()).lower()
    except Exception:
        body_text = ""

    if any(marker in body_text for marker in ("comments are closed", "评论已关闭", "closed for comments")):
        return False, "页面提示评论已关闭"
    if any(marker in body_text for marker in ("log in to comment", "you must be logged in", "sign in to comment", "登录后评论")):
        return False, "页面提示必须登录后才能评论"
    if any(marker in body_text for marker in ("leave a reply", "post a comment", "comment as", "发表评论", "评论")):
        return True, "页面文本包含评论区提示词"
    return False, "DOM 与页面文本均未发现评论区线索"


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
    target_url = str(target.get("url", "") or task_row.get("目标网站", "") or "")
    return {
        "来源链接": normalized_url,
        "来源标题": str(task_row.get("来源标题", "") or ""),
        "根域名": str(task_row.get("根域名", "") or ""),
        "页面评分": str(task_row.get("页面评分", "") or ""),
        "目标站标识": str(target.get("site_key", "") or ""),
        "目标网站": target_url,
        "状态": STATUS_PENDING_RETRY,
        "最后尝试时间": timestamp,
        "最近失败时间": timestamp,
        "最近失败原因": summarize_result_message(reason),
        "最后更新时间": timestamp,
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

def _deep_scroll_to_bottom(page: Page):
    """
    分段深度滚动，模拟真人翻阅并触发长页面的懒加载。
    针对评论极多的页面（如 100+ 评论）至关重要。
    """
    print("  🌀 正在进行深度滚动，寻找页面底部的评论区...")
    for i in range(5):  # 最多滚 5 屏
        page.evaluate("window.scrollBy(0, 2000)")
        time.sleep(1.5) # 给点时间让内容蹦出来
        # 实时检查是否已经看到评论框，看到了就提前收工
        if page.locator('textarea, [contenteditable="true"]').first.is_visible():
            print(f"  ✨ 在第 {i+1} 次滚动时提前发现评论框！")
            return
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
    }

    for attempt in range(max_retries):
        if attempt > 0:
            print(f"  ⏳ 第 {attempt + 1} 次重试...")
            time.sleep(5)

        try:
            nav_meta = _fast_navigate_for_commenting(page, url, page_load_timeout_ms)
            meta.update(nav_meta)

            # 【预处理】关闭 GDPR Cookie 弹窗
            try_dismiss_overlays(page)

            # 【优化】深滚动触发评论框加载 (针对长页面/懒加载)
            _deep_scroll_to_bottom(page)

            # ===== Layer 1：传统 DOM 识别 =====
            layer1_result = _try_dom_post(page, comment_content, name, email, website, anchor_text=anchor_text)
            if layer1_result[0]:
                meta["diagnostic_category"] = "dom_success"
                return True, layer1_result[1], meta
            last_error = summarize_result_message(layer1_result[1])

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

                from strategy_decider import decide_next_strategy, should_try_vision, should_skip as sd_should_skip

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
                    # AI 判断可以尝试 Vision（或其他策略）
                    should_probe_vision, reason = _page_has_comment_signals(page)
                    if should_probe_vision or should_try_vision(decision):
                        print(f"  🤖 Layer 4：调用 Gemini Vision AI 分析网页截图（AI 决策：{decision.get('action')}）...")
                        from vision_agent import try_post_via_vision

                        vision_result = try_post_via_vision(page, comment_content)
                        meta.update(vision_result[2])
                        if vision_result[0]:
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
    current_url = str(page.url)
    original_url = str(getattr(page, "_original_url", ""))
    if current_url and original_url and current_url != original_url:
        return f"URL 已从 {original_url} 跳转为 {current_url}"

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
    
    # 方式 1：主页面传统 textarea
    textareas = page.locator('textarea:visible')
    if textareas.count() > 0:
        print("  👁️ 找到 <textarea> 输入框，正在填写...")
        # 尝试填写额外字段
        _fill_additional_fields(page, name, email, website)
        
        ta = textareas.first
        ta.scroll_into_view_if_needed()
        ta.fill(comment_content)
        time.sleep(1)
        return _try_submit(page, comment_content, target_url=website, anchor_text=anchor_text)
    
    # 方式 2：主页面 contenteditable 富文本框
    content_editables = page.locator('[contenteditable="true"]:visible')
    if content_editables.count() > 0:
        print("  👁️ 找到 contenteditable 富文本评论框，正在填写...")
        # 尝试填写额外字段
        _fill_additional_fields(page, name, email, website)
        
        ce = content_editables.first
        ce.scroll_into_view_if_needed()
        ce.click()
        time.sleep(0.3)
        page.keyboard.type(comment_content, delay=30)
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
                
                # 情况 A: 内部有 textarea
                frame_textareas = frame.locator('textarea:visible')
                if frame_textareas.count() > 0:
                    print(f"  👁️ 在 iframe 内发现 <textarea>，准备开始填写...")
                    if "blogger.com" in frame_url or "blogblog.com" in frame_url:
                        _handle_blogger_identity(frame)
                    _fill_additional_fields(frame, name, email, website)
                    ft = frame_textareas.first
                    ft.click(timeout=3000)
                    ft.fill(comment_content)
                    return True, _try_submit_in_frame(
                        frame, page, comment_content, target_url=website, anchor_text=anchor_text
                    )
                
                # 情况 B: 内部有 contenteditable
                frame_editables = frame.locator('[contenteditable="true"]:visible')
                if frame_editables.count() > 0:
                    print(f"  👁️ 在 iframe 内发现富文本框，准备开始填写...")
                    if "blogger.com" in frame_url or "blogblog.com" in frame_url:
                        _handle_blogger_identity(frame)
                    _fill_additional_fields(frame, name, email, website)
                    fe = frame_editables.first
                    fe.click(timeout=3000)
                    fe.type(comment_content, delay=30)
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

    if not url:
        raise ValueError("任务来源链接为空，无法执行发帖。")
    
    print(f"\n🚀 开始处理来源 {url} -> 站点 {target.get('site_key', '')}")
    if link_format_analysis:
        print(
            "  🔎 Link_Format 预判："
            f"{link_format_analysis.get('recommended_format', 'unknown')} | "
            f"证据={link_format_analysis.get('evidence_type', 'unknown')} | "
            f"置信度={link_format_analysis.get('confidence', 0)}"
        )
    
    # 步骤 1：AI 生成文案
    print("  [1/3] 🤖 AI 正在生成外链推广文案...")
    page_context = fetch_page_context(url)
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
        target_name = active_target.get("anchor_text", "Anonymous").title()
        target_email = str(active_target.get("email", "") or active_target.get("联系邮箱", "") or DEFAULT_CONTACT_EMAIL)
        target_url = active_target["url"]
        
        print(f"  📋 使用飞书目标站配置：锚文本='{active_target.get('anchor_text', '')}' -> {target_url}")
        content_bundle = generate_localized_bundle_for_target(active_target, generation_link_format, page_context)
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
        from ai_generator import translate_comment_to_chinese

        comment_content_zh = translate_comment_to_chinese(comment_content)
    
    # 步骤 2：自动发帖（带重试机制）
    print("  [2/3] 🌐 开始接管真实 Chrome 进行自动发帖...")
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
    )
    print(f"  🤖 发帖结果: {result_msg}")
    
    # 步骤 3：结果写回飞书状态表
    print("  [3/3] 📝 更新结果至飞书状态表...")
    notes_message = format_notes(
        summarize_result_message(result_msg),
        run_meta.get("diagnosis", ""),
    )
    last_updated_value = time.strftime('%Y-%m-%d %H:%M:%S')

    updates = {
        "来源链接": url,
        "来源标题": str(task_row.get("来源标题", "") or ""),
        "根域名": str(task_row.get("根域名", "") or ""),
        "页面评分": str(task_row.get("页面评分", "") or ""),
        "目标站标识": str(target.get("site_key", "") or ""),
        "目标网站": target_url,
        "最后尝试时间": last_updated_value,
        "当前评论内容": comment_content,
        "当前评论内容中文": comment_content_zh,
        "当前锚文本": anchor_text,
        "关键词": keywords,
        "链接格式": link_format,
        "来源类型": str(task_row.get("来源类型", "") or ""),
        "有网址字段": str(task_row.get("有网址字段", "") or ""),
        "有验证码": str(task_row.get("有验证码", "") or ""),
        "最后更新时间": last_updated_value,
    }
    
    if is_success:
        updates["状态"] = STATUS_SUCCESS
        updates["最近成功时间"] = last_updated_value
        updates["成功链接"] = url
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
    }
    return result



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

    print(f"🕸️ 正在连接你的本地 Chrome 浏览器（{cdp_url}）...")
    with sync_playwright() as p:
        try:
            browser_app = p.chromium.connect_over_cdp(cdp_url)
            print("🤩 成功接管本地真实 Chrome！指纹认证已生效。\n")
            context = browser_app.contexts[0]
            work_page = context.new_page()
            _maybe_bring_to_front(work_page, browser_cfg)

            for idx, task in enumerate(tasks, start=1):
                if work_page.is_closed():
                    work_page = context.new_page()
                _maybe_bring_to_front(work_page, browser_cfg)
                print(f"\n>>> 进度: {idx}/{len(tasks)}")
                try:
                    res = process_task(
                        task["status_row"],
                        task["target"],
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
                    print(f"  ❌ 单条任务异常，已回退为待重试: {reason}")
                    workbook.upsert_sheet_dict(
                        "records",
                        STATUS_HEADERS,
                        ["来源链接", "目标站标识"],
                        _build_task_failure_updates(task["status_row"], task["target"], reason, last_updated_value),
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
                    }
                    if not work_page.is_closed():
                        work_page.close()
                    work_page = context.new_page()
                    _maybe_bring_to_front(work_page, browser_cfg)
                if res["success"]:
                    success_list.append(res)
                else:
                    failed_list.append(res)

            if not work_page.is_closed():
                work_page.close()
            browser_app.close()

            if send_report:
                from webhook_sender import create_webhook_sender
                sender = create_webhook_sender()
                if sender:
                    summary = {"success": success_list, "failed": failed_list}
                    title = f"🌍 外链自动化执行报告 | 成功 {len(success_list)} | 失败 {len(failed_list)}"
                    sender.send_detailed_report(title, summary)
                else:
                    print("\nℹ️ 未配置飞书 Webhook，跳过通知。")

        except Exception as e:
            print(f"❌ 接管本地浏览器失败（请确认是通过 Start_Robot.command 启动的）: {e}")
            failed_list.append({"url": "", "reason": str(e), "success": False})

    sync_reporting_workbook(workbook=workbook)

    return {
        "success": success_list,
        "failed": failed_list,
        "today_tasks": len(tasks),
    }


def main():
    run_once(send_report=True)

if __name__ == '__main__':
    main()
