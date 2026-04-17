"""
vision_agent.py
=================
Gemini Vision 多模态视觉代理模块
"""

import json
import os
import re
import time
from io import BytesIO
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from PIL import Image
from playwright.sync_api import Page
from gemini_key_manager import get_active_key

load_dotenv()

COMMENT_FOCUS_SELECTORS = (
    'iframe[src*="comment"]',
    'iframe[src*="blogger"]',
    'iframe[src*="disqus"]',
    'iframe[src*="reply"]',
    "#comments",
    ".comments-area",
    ".comments",
    ".commentlist",
    ".comment-list",
    ".responses",
    ".discussion",
    ".comment-respond",
    "#respond",
    ".comment-form",
    "#commentform",
    "ol.comment-list",
    "ol.commentlist",
    'section[id*="comment"]',
    'div[id*="comment"]',
    'form[id*="comment"]',
    'form[class*="comment"]',
    'textarea',
    '[contenteditable="true"]',
)

VISION_DEFAULTS = {
    "enabled": True,
    "debug_dir": "artifacts/vision",
    "model": "gemini-3-flash-preview",
    "fallback_model": "gemini-2.5-flash",
    "request_timeout_seconds": 30,
    "retry_attempts": 2,
    "retry_backoff_seconds": 2,
    "image_type": "jpeg",
    "image_quality": 65,
    "max_image_side": 1400,
    "circuit_breaker_file": "artifacts/vision/circuit_breaker.json",
    "circuit_breaker_failures": 3,
    "circuit_breaker_cooldown_seconds": 900,
}

_GEMINI_CLIENT_CACHE: dict[tuple[str, int], Any] = {}


def load_vision_config(config_path: str = "config.json") -> dict:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return {**VISION_DEFAULTS, **config.get("vision", {})}
    except Exception:
        return dict(VISION_DEFAULTS)


def _get_gemini_client(config_path: str = "config.json"):
    from google import genai
    from google.genai import types

    api_key = get_active_key()
    if not api_key:
        raise ValueError("环境变量 GEMINI_API_KEY 未配置，无法使用 Vision 模块！")
    config = load_vision_config(config_path)
    timeout = int(config.get("request_timeout_seconds", 30) or 30)
    cache_key = (api_key, timeout)
    cached = _GEMINI_CLIENT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=timeout,
            retry_options=types.HttpRetryOptions(
                attempts=1,
            ),
        ),
    )
    _GEMINI_CLIENT_CACHE[cache_key] = client
    return client


def _build_debug_dir(config_path: str = "config.json") -> Path:
    config = load_vision_config(config_path)
    base_dir = Path(config["debug_dir"])
    run_dir = base_dir / datetime.now().strftime("%Y-%m-%d")
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return cleaned[:60] or "page"


def _save_debug_artifacts(
    debug_dir: Path,
    prefix: str,
    screenshot_bytes: bytes,
    image_ext: str,
    raw_text: str,
    parsed_json: Optional[dict[str, Any]],
    meta: dict[str, Any],
) -> None:
    (debug_dir / f"{prefix}.{image_ext}").write_bytes(screenshot_bytes)
    (debug_dir / f"{prefix}.txt").write_text(raw_text or "", encoding="utf-8")
    if parsed_json is not None:
        (debug_dir / f"{prefix}.json").write_text(
            json.dumps(parsed_json, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    (debug_dir / f"{prefix}.meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _extract_json(raw_text: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    clean_text = re.sub(r"```(?:json)?\s*", "", raw_text or "").replace("```", "").strip()
    json_match = re.search(r"\{.*\}", clean_text, re.DOTALL)
    if not json_match:
        return None, "vision_invalid_json"
    try:
        return json.loads(json_match.group()), None
    except json.JSONDecodeError:
        return None, "vision_invalid_json"


def _capture_page_state(page: Page) -> dict[str, Any]:
    viewport = page.viewport_size or {"width": 1280, "height": 800}
    scroll = page.evaluate(
        "() => ({ x: window.scrollX || 0, y: window.scrollY || 0, innerHeight: window.innerHeight || 0 })"
    )
    return {
        "url": page.url,
        "viewport": viewport,
        "scroll": scroll,
    }


def _build_screenshot_options(config: dict) -> tuple[dict[str, Any], str, str]:
    image_type = str(config.get("image_type", "jpeg") or "jpeg").strip().lower()
    quality = int(config.get("image_quality", 65) or 65)
    if image_type not in {"jpeg", "png", "webp"}:
        image_type = "jpeg"

    options: dict[str, Any] = {"type": image_type}
    if image_type in {"jpeg", "webp"}:
        options["quality"] = max(1, min(100, quality))
    image_ext = "jpg" if image_type == "jpeg" else image_type
    mime_type = f"image/{image_type}"
    return options, mime_type, image_ext


def _resize_image_for_vision(
    screenshot_bytes: bytes,
    mime_type: str,
    image_ext: str,
    config: dict,
) -> tuple[bytes, str, str, dict[str, Any]]:
    max_side = int(config.get("max_image_side", 1400) or 1400)
    meta = {"image_ext": image_ext, "mime_type": mime_type}
    if max_side <= 0:
        return screenshot_bytes, mime_type, image_ext, meta

    try:
        image = Image.open(BytesIO(screenshot_bytes))
        width, height = image.size
        meta["original_size"] = {"width": width, "height": height}
        if max(width, height) <= max_side:
            meta["resized"] = False
            return screenshot_bytes, mime_type, image_ext, meta

        resized = image.copy()
        resized.thumbnail((max_side, max_side))
        output = BytesIO()
        save_format = "JPEG" if image_ext == "jpg" else image_ext.upper()
        if save_format == "JPEG" and resized.mode not in {"RGB", "L"}:
            resized = resized.convert("RGB")
        save_kwargs = {"optimize": True}
        if save_format in {"JPEG", "WEBP"}:
            save_kwargs["quality"] = int(config.get("image_quality", 65) or 65)
        resized.save(output, format=save_format, **save_kwargs)
        meta["resized"] = True
        meta["resized_size"] = {"width": resized.size[0], "height": resized.size[1]}
        return output.getvalue(), mime_type, image_ext, meta
    except Exception as exc:
        meta["resized"] = False
        meta["resize_error"] = str(exc)
        return screenshot_bytes, mime_type, image_ext, meta


def _is_retryable_vision_error(exc: Exception) -> bool:
    message = str(exc or "").lower()
    return any(
        marker in message
        for marker in (
            "timed out",
            "timeout",
            "handshake operation timed out",
            "_ssl.c:",
            "deadline exceeded",
            "connection reset",
            "temporarily unavailable",
            "service unavailable",
            "internal server error",
            "429",
            "503",
        )
    )


def _circuit_breaker_path(config: dict) -> Path:
    path = Path(str(config.get("circuit_breaker_file", VISION_DEFAULTS["circuit_breaker_file"]) or VISION_DEFAULTS["circuit_breaker_file"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_circuit_breaker(config: dict) -> dict[str, Any]:
    path = _circuit_breaker_path(config)
    if not path.exists():
        return {"consecutive_failures": 0, "opened_until": ""}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {"consecutive_failures": 0, "opened_until": ""}
        return {
            "consecutive_failures": int(payload.get("consecutive_failures", 0) or 0),
            "opened_until": str(payload.get("opened_until", "") or ""),
        }
    except Exception:
        return {"consecutive_failures": 0, "opened_until": ""}


def _save_circuit_breaker(config: dict, state: dict[str, Any]) -> None:
    path = _circuit_breaker_path(config)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _get_circuit_breaker_pause_reason(config: dict) -> str:
    state = _load_circuit_breaker(config)
    opened_until = str(state.get("opened_until", "") or "").strip()
    if not opened_until:
        return ""
    try:
        resume_at = datetime.fromisoformat(opened_until)
    except ValueError:
        return ""
    remaining = int((resume_at - datetime.now()).total_seconds())
    if remaining <= 0:
        _save_circuit_breaker(config, {"consecutive_failures": 0, "opened_until": ""})
        return ""
    return f"Vision 熔断中，约 {remaining} 秒后自动恢复"


def _record_vision_success(config: dict) -> None:
    _save_circuit_breaker(config, {"consecutive_failures": 0, "opened_until": ""})


def _record_vision_failure(config: dict, exc: Exception) -> None:
    if not _is_retryable_vision_error(exc):
        return
    state = _load_circuit_breaker(config)
    failures = int(state.get("consecutive_failures", 0) or 0) + 1
    threshold = max(1, int(config.get("circuit_breaker_failures", 3) or 3))
    cooldown = max(60, int(config.get("circuit_breaker_cooldown_seconds", 900) or 900))
    opened_until = ""
    if failures >= threshold:
        opened_until = (datetime.now() + timedelta(seconds=cooldown)).isoformat(timespec="seconds")
    _save_circuit_breaker(
        config,
        {
            "consecutive_failures": failures,
            "opened_until": opened_until,
        },
    )


def _generate_content_with_retry(client, model: str, contents: list[Any], config: dict):
    attempts = max(1, int(config.get("retry_attempts", 2) or 2))
    backoff = float(config.get("retry_backoff_seconds", 2) or 2)
    last_exc = None
    model_candidates = [str(model or "").strip(), str(config.get("fallback_model", "") or "").strip()]
    deduped_models = [item for idx, item in enumerate(model_candidates) if item and item not in model_candidates[:idx]]
    for model_name in deduped_models:
        for attempt in range(attempts):
            try:
                return client.models.generate_content(
                    model=model_name,
                    contents=contents,
                )
            except Exception as exc:
                last_exc = exc
                if attempt >= attempts - 1 or not _is_retryable_vision_error(exc):
                    break
                time.sleep(backoff * (attempt + 1))
    raise last_exc or RuntimeError("Vision generate_content 未返回结果。")


def _build_focus_clip(box: dict[str, float], state: dict[str, Any], pad_x: int = 220, pad_y: int = 180) -> dict[str, float]:
    viewport = state.get("viewport", {}) or {}
    scroll = state.get("scroll", {}) or {}
    viewport_width = float(viewport.get("width") or 1280)
    viewport_height = float(scroll.get("innerHeight") or viewport.get("height") or 800)
    scroll_y = float(scroll.get("y") or 0)
    scroll_x = float(scroll.get("x") or 0)

    left = max(float(box.get("x") or 0) - pad_x, scroll_x)
    top = max(float(box.get("y") or 0) - pad_y, scroll_y)
    right = min(float(box.get("x") or 0) + float(box.get("width") or 0) + pad_x, scroll_x + viewport_width)
    bottom = min(float(box.get("y") or 0) + float(box.get("height") or 0) + pad_y, scroll_y + viewport_height)

    width = max(1.0, right - left)
    height = max(1.0, bottom - top)
    return {
        "x": left,
        "y": top,
        "width": width,
        "height": height,
    }


def _capture_focused_comment_region(page: Page, state: dict[str, Any], config: dict) -> tuple[bytes, str, str, dict[str, Any]]:
    screenshot_options, mime_type, image_ext = _build_screenshot_options(config)
    for selector in COMMENT_FOCUS_SELECTORS:
        try:
            locator = page.locator(selector)
            if locator.count() <= 0:
                continue

            try:
                locator.first.scroll_into_view_if_needed(timeout=3000)
            except Exception:
                pass

            if not locator.first.is_visible():
                continue
            box = locator.first.bounding_box()
            if box:
                clip = _build_focus_clip(box, state)
                image_bytes = page.screenshot(full_page=False, clip=clip, **screenshot_options)
                image_bytes, mime_type, image_ext, resize_meta = _resize_image_for_vision(
                    image_bytes,
                    mime_type,
                    image_ext,
                    config,
                )
                return (
                    image_bytes,
                    mime_type,
                    image_ext,
                    {
                        "capture_mode": "clip",
                        "focus_selector": selector,
                        "clip": clip,
                        **resize_meta,
                    },
                )

            image_bytes = locator.first.screenshot(timeout=3000, **screenshot_options)
            image_bytes, mime_type, image_ext, resize_meta = _resize_image_for_vision(
                image_bytes,
                mime_type,
                image_ext,
                config,
            )
            return (
                image_bytes,
                mime_type,
                image_ext,
                {
                    "capture_mode": "locator",
                    "focus_selector": selector,
                    **resize_meta,
                },
            )
        except Exception:
            continue

    image_bytes = page.screenshot(full_page=False, **screenshot_options)
    image_bytes, mime_type, image_ext, resize_meta = _resize_image_for_vision(
        image_bytes,
        mime_type,
        image_ext,
        config,
    )
    return (
        image_bytes,
        mime_type,
        image_ext,
        {
            "capture_mode": "viewport",
            "focus_selector": "",
            **resize_meta,
        },
    )


def _request_vision_analysis(page: Page, debug_dir: Path, stage: str, config_path: str = "config.json") -> dict[str, Any]:
    print(f"  📸 正在截取网页快照（{stage}），准备发给 AI 视觉模型分析...")
    config = load_vision_config(config_path)
    pause_reason = _get_circuit_breaker_pause_reason(config)
    if pause_reason:
        return {
            "ok": False,
            "error_code": "vision_temporarily_paused",
            "raw_text": pause_reason,
            "coords": None,
            "debug_prefix": "",
        }
    state = _capture_page_state(page)
    screenshot_bytes, mime_type, image_ext, capture_meta = _capture_focused_comment_region(page, state, config)
    width = state["viewport"]["width"]
    height = state["viewport"]["height"]
    state = {
        **state,
        **capture_meta,
    }

    prompt = f"""
这是一张网页的屏幕截图，尺寸为 {width} x {height} 像素。
当前滚动位置大约在 Y={state["scroll"]["y"]}。

请严格返回 JSON，不要加解释。任务：
1. 找出评论/留言输入区域中心点坐标
2. 找出提交按钮中心点坐标
3. 如果存在遮挡层或弹窗，找出关闭按钮坐标

返回格式：
{{
  "textarea_x": <number|null>,
  "textarea_y": <number|null>,
  "submit_x": <number|null>,
  "submit_y": <number|null>,
  "overlay_close_x": <number|null>,
  "overlay_close_y": <number|null>
}}
"""

    try:
        client = _get_gemini_client(config_path)
        from google.genai import types

        response = _generate_content_with_retry(
            client,
            str(config.get("model", "gemini-3-flash-preview") or "gemini-3-flash-preview"),
            [
                types.Part.from_bytes(data=screenshot_bytes, mime_type=mime_type),
                prompt,
            ],
            config,
        )
        raw_text = response.text.strip()
        parsed_json, error_code = _extract_json(raw_text)
        prefix = f"{datetime.now().strftime('%H%M%S')}-{stage}"
        _save_debug_artifacts(debug_dir, prefix, screenshot_bytes, image_ext, raw_text, parsed_json, state)
        _record_vision_success(config)

        if error_code:
            return {
                "ok": False,
                "error_code": error_code,
                "raw_text": raw_text,
                "coords": None,
                "debug_prefix": prefix,
            }
        return {
            "ok": True,
            "error_code": None,
            "raw_text": raw_text,
            "coords": parsed_json,
            "debug_prefix": prefix,
        }
    except Exception as exc:
        _record_vision_failure(config, exc)
        prefix = f"{datetime.now().strftime('%H%M%S')}-{stage}"
        _save_debug_artifacts(debug_dir, prefix, screenshot_bytes, image_ext, str(exc), None, state)
        return {
            "ok": False,
            "error_code": "vision_api_error",
            "raw_text": str(exc),
            "coords": None,
            "debug_prefix": prefix,
        }


def analyze_page_for_comment_area(page: Page, stage: str = "initial") -> dict[str, Any]:
    debug_dir = _build_debug_dir()
    return _request_vision_analysis(page, debug_dir, stage)


def analyze_link_format_capability(page: Page, stage: str = "format-capability", config_path: str = "config.json") -> dict[str, Any]:
    print(f"  📸 正在截取网页快照（{stage}），准备让 AI 判断评论格式能力...")
    config = load_vision_config(config_path)
    pause_reason = _get_circuit_breaker_pause_reason(config)
    if pause_reason:
        return {
            "ok": False,
            "error_code": "vision_temporarily_paused",
            "raw_text": pause_reason,
            "result": None,
            "debug_prefix": "",
        }
    debug_dir = _build_debug_dir()
    state = _capture_page_state(page)
    screenshot_bytes, mime_type, image_ext, capture_meta = _capture_focused_comment_region(page, state, config)
    width = state["viewport"]["width"]
    height = state["viewport"]["height"]
    state = {
        **state,
        **capture_meta,
    }

    prompt = f"""
这是一张网页评论区域附近的屏幕截图，尺寸为 {width} x {height} 像素。
本次截图模式是 {state.get("capture_mode", "viewport")}，焦点选择器是 {state.get("focus_selector", "") or "none"}。
请你判断这个页面当前可见的评论系统，更像支持哪种外链格式能力。

严格返回 JSON，不要加解释：
{{
  "recommended_format": "html" | "plain_text_autolink" | "plain_text" | "unknown",
  "evidence_type": "<short_code>",
  "confidence": <0到1之间的小数>,
  "reason": "<一句简短中文说明>"
}}

判断原则：
1. 如果看到富文本编辑器、可点击锚文本样式、Blogger/WordPress 评论编辑框、带 website/url 字段、或明显能接受带锚文本链接的评论，优先给 "html"
2. 如果看到历史评论里裸 URL 会自动渲染成链接，但没有明显富文本能力，给 "plain_text_autolink"
3. 如果只像普通纯文本留言框，给 "plain_text"
4. 如果看不清、证据不足，给 "unknown"
"""

    try:
        client = _get_gemini_client(config_path)
        from google.genai import types

        response = _generate_content_with_retry(
            client,
            str(config.get("model", "gemini-3-flash-preview") or "gemini-3-flash-preview"),
            [
                types.Part.from_bytes(data=screenshot_bytes, mime_type=mime_type),
                prompt,
            ],
            config,
        )
        raw_text = response.text.strip()
        parsed_json, error_code = _extract_json(raw_text)
        prefix = f"{datetime.now().strftime('%H%M%S')}-{stage}"
        _save_debug_artifacts(debug_dir, prefix, screenshot_bytes, image_ext, raw_text, parsed_json, state)

        if error_code or not parsed_json:
            return {
                "ok": False,
                "error_code": error_code or "vision_invalid_json",
                "raw_text": raw_text,
                "result": None,
                "debug_prefix": prefix,
            }

        result = {
            "recommended_format": str(parsed_json.get("recommended_format", "unknown") or "unknown").strip(),
            "evidence_type": str(parsed_json.get("evidence_type", "vision_format_capability") or "vision_format_capability").strip(),
            "confidence": float(parsed_json.get("confidence", 0) or 0),
            "reason": str(parsed_json.get("reason", "") or "").strip(),
        }
        _record_vision_success(config)
        return {
            "ok": True,
            "error_code": None,
            "raw_text": raw_text,
            "result": result,
            "debug_prefix": prefix,
        }
    except Exception as exc:
        _record_vision_failure(config, exc)
        prefix = f"{datetime.now().strftime('%H%M%S')}-{stage}"
        _save_debug_artifacts(debug_dir, prefix, screenshot_bytes, image_ext, str(exc), None, state)
        return {
            "ok": False,
            "error_code": "vision_api_error",
            "raw_text": str(exc),
            "result": None,
            "debug_prefix": prefix,
        }


def _format_failure_message(code: str, details: str = "") -> str:
    mapping = {
        "vision_api_error": "Vision API 调用失败",
        "vision_temporarily_paused": "Vision 暂时熔断跳过",
        "vision_invalid_json": "Vision 返回结果无法解析为 JSON",
        "overlay_blocked": "弹窗关闭后仍未识别到评论区",
        "textarea_not_found": "Vision 未识别到评论输入框坐标",
        "submit_not_found": "Vision 未识别到提交按钮坐标",
        "click_no_effect": "Vision 点击评论框后未能稳定输入",
        "post_verify_failed": "Vision 点击提交后未通过结果校验",
    }
    base = mapping.get(code, code)
    return f"{base}: {details}" if details else base


def try_post_via_vision(page: Page, comment_content: str) -> tuple[bool, str, dict[str, Any]]:
    config = load_vision_config()
    if not config.get("enabled", True):
        meta = {"used_vision": False, "diagnostic_category": "vision_disabled"}
        return False, "Vision 已在配置中关闭。", meta

    page.wait_for_timeout(1200)
    analysis = analyze_page_for_comment_area(page, stage="initial")
    used_vision = True
    meta = {
        "used_vision": used_vision,
        "diagnostic_category": analysis.get("error_code") or "",
        "vision_debug_prefix": analysis.get("debug_prefix", ""),
    }
    if not analysis["ok"]:
        return False, _format_failure_message(analysis["error_code"], analysis.get("raw_text", "")), meta

    coords = analysis["coords"] or {}
    if coords.get("overlay_close_x") and coords.get("overlay_close_y"):
        print("  🛑 Vision AI 发现遮挡弹窗，正在关闭并重新识别...")
        page.mouse.click(coords["overlay_close_x"], coords["overlay_close_y"])
        time.sleep(1)
        page.wait_for_timeout(1000)
        analysis = analyze_page_for_comment_area(page, stage="after-overlay")
        meta["vision_debug_prefix"] = analysis.get("debug_prefix", meta["vision_debug_prefix"])
        if not analysis["ok"]:
            meta["diagnostic_category"] = analysis["error_code"]
            return False, _format_failure_message(analysis["error_code"], analysis.get("raw_text", "")), meta
        coords = analysis["coords"] or {}
        if not coords.get("textarea_x") or not coords.get("textarea_y"):
            meta["diagnostic_category"] = "overlay_blocked"
            return False, _format_failure_message("overlay_blocked"), meta

    tx = coords.get("textarea_x")
    ty = coords.get("textarea_y")
    if not tx or not ty:
        meta["diagnostic_category"] = "textarea_not_found"
        return False, _format_failure_message("textarea_not_found"), meta

    print(f"  📍 Vision AI 定位到评论框坐标：({tx}, {ty})，正在填写...")
    page.mouse.click(tx, ty)
    time.sleep(0.5)
    page.keyboard.type(comment_content, delay=25)
    time.sleep(0.8)

    focused = page.evaluate(
        "() => { const el = document.activeElement; return !!el && (el.tagName === 'TEXTAREA' || el.isContentEditable); }"
    )
    if not focused:
        meta["diagnostic_category"] = "click_no_effect"
        return False, _format_failure_message("click_no_effect"), meta

    sx = coords.get("submit_x")
    sy = coords.get("submit_y")
    if not sx or not sy:
        meta["diagnostic_category"] = "submit_not_found"
        return False, _format_failure_message("submit_not_found"), meta

    print(f"  👉 Vision AI 定位到提交按钮坐标：({sx}, {sy})，正在点击...")
    page.mouse.click(sx, sy)

    from form_automation_local import _verify_post_success

    is_success, msg = _verify_post_success(page, comment_content)
    if is_success:
        meta["diagnostic_category"] = "vision_success"
        return True, f"Vision AI 视觉定位成功，并验证发帖成功: {msg}", meta

    meta["diagnostic_category"] = "post_verify_failed"
    return False, _format_failure_message("post_verify_failed", msg), meta
