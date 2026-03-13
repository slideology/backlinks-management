"""
vision_agent.py
=================
Gemini Vision 多模态视觉代理模块
"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from playwright.sync_api import Page

load_dotenv()

VISION_DEFAULTS = {
    "enabled": True,
    "debug_dir": "artifacts/vision",
}


def load_vision_config(config_path: str = "config.json") -> dict:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return {**VISION_DEFAULTS, **config.get("vision", {})}
    except Exception:
        return dict(VISION_DEFAULTS)


def _get_gemini_client():
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("环境变量 GEMINI_API_KEY 未配置，无法使用 Vision 模块！")
    return genai.Client(api_key=api_key)


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
    raw_text: str,
    parsed_json: Optional[dict[str, Any]],
    meta: dict[str, Any],
) -> None:
    (debug_dir / f"{prefix}.png").write_bytes(screenshot_bytes)
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


def _request_vision_analysis(page: Page, debug_dir: Path, stage: str) -> dict[str, Any]:
    print(f"  📸 正在截取网页快照（{stage}），准备发给 AI 视觉模型分析...")
    screenshot_bytes = page.screenshot(full_page=False)
    state = _capture_page_state(page)
    width = state["viewport"]["width"]
    height = state["viewport"]["height"]

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
        client = _get_gemini_client()
        from google.genai import types

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=screenshot_bytes, mime_type="image/png"),
                prompt,
            ],
        )
        raw_text = response.text.strip()
        parsed_json, error_code = _extract_json(raw_text)
        prefix = f"{datetime.now().strftime('%H%M%S')}-{stage}"
        _save_debug_artifacts(debug_dir, prefix, screenshot_bytes, raw_text, parsed_json, state)

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
        prefix = f"{datetime.now().strftime('%H%M%S')}-{stage}"
        _save_debug_artifacts(debug_dir, prefix, screenshot_bytes, str(exc), None, state)
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


def _format_failure_message(code: str, details: str = "") -> str:
    mapping = {
        "vision_api_error": "Vision API 调用失败",
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
