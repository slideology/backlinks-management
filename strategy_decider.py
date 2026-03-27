"""
strategy_decider.py
====================
AI 策略决策器模块（第一阶段 Agent 化改造核心）

功能说明：
  在发帖失败后，AI 会根据「失败截图 + 错误信息 + 站点 URL」
  智能判断下一步最优策略，而不是死板地按固定顺序执行。

策略枚举：
  - retry_dom   → 重新尝试 DOM 方式（如超时、弹窗遮挡等临时问题）
  - try_vision  → 升级到 Vision AI 截图点击（DOM 找不到但有评论信号）
  - try_sso     → 尝试 Google SSO 登录（检测到登录墙）
  - skip        → 跳过（无评论区、评论关闭、强验证码保护等）
  - mark_blacklist → 标记为长期跳过（已识别为无价值站点）

使用方法：
  from strategy_decider import decide_next_strategy

  screenshot_bytes = page.screenshot()
  strategy = decide_next_strategy(
      error_code="dom_not_found",
      error_message="Layer 1 未找到评论框",
      screenshot_bytes=screenshot_bytes,
      site_url="https://example.com/blog/post",
      config_path="config.json",
  )
  # strategy 返回: {"action": "try_vision", "reason": "...", "confidence": 0.9}
"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv()

# =====================================================================
# 常量定义
# =====================================================================

# 所有可选策略
STRATEGY_RETRY_DOM = "retry_dom"
STRATEGY_TRY_VISION = "try_vision"
STRATEGY_TRY_SSO = "try_sso"
STRATEGY_SKIP = "skip"
STRATEGY_MARK_BLACKLIST = "mark_blacklist"

# 决策器默认配置
DECIDER_DEFAULTS = {
    "enabled": True,
    "model": "gemini-2.0-flash",        # 使用快速模型降低延迟和成本
    "request_timeout_seconds": 15,
    "debug_dir": "artifacts/strategy_decider",
    "confidence_threshold": 0.6,         # 置信度低于此值时回退到规则决策
    "rule_based_fallback": True,         # AI 失败时启用规则兜底
}

# =====================================================================
# 快速规则决策（无需调用 AI，零延迟，用于明显情况）
# =====================================================================

# 错误码 → 不需要 AI 直接决策的明显策略映射
_RULE_BASED_STRATEGY_MAP = {
    # DOM 找不到评论框 → 尝试 Vision
    "dom_not_found": STRATEGY_TRY_VISION,
    # Vision 超时 → 跳过（避免循环等待）
    "vision_api_error": STRATEGY_SKIP,
    "vision_temporarily_paused": STRATEGY_SKIP,
    # Vision 识别不到评论框 → 跳过（页面确实没有）
    "vision_invalid_json": STRATEGY_RETRY_DOM,
    # 弹窗遮挡后仍失败 → 尝试 Vision
    "overlay_blocked": STRATEGY_TRY_VISION,
    # Vision 成功识别但点击无效 → 跳过（焦点问题，重试意义不大）
    "click_no_effect": STRATEGY_SKIP,
    # 提交后验证失败 → 标记黑名单（已提交但无法确认，避免重复发帖）
    "post_verify_failed": STRATEGY_SKIP,
}

# 错误信息中的关键词 → 策略（用于规则兜底）
_KEYWORD_STRATEGY_MAP = [
    # 登录墙 → 尝试 SSO
    (["log in to comment", "you must be logged in", "sign in", "登录后评论"], STRATEGY_TRY_SSO),
    # 评论明确关闭 → 标记黑名单
    (["comments are closed", "评论已关闭", "closed for comments"], STRATEGY_MARK_BLACKLIST),
    # 页面超时但有内容 → 重试 DOM（网络波动）
    (["timeout", "timed out", "net::err_timed_out"], STRATEGY_RETRY_DOM),
    # 验证码保护 → 跳过
    (["recaptcha", "captcha", "bot protection", "cloudflare"], STRATEGY_SKIP),
    # SSO 登录后又失败 → Vision
    (["sso_success", "sso 登录成功"], STRATEGY_TRY_VISION),
]


def _rule_based_decide(error_code: str, error_message: str) -> Optional[dict]:
    """
    基于规则的快速决策（不调用 AI）。
    返回 None 表示规则无法判断，需要上升到 AI 决策。
    """
    # 1. 先查错误码映射表
    if error_code and error_code in _RULE_BASED_STRATEGY_MAP:
        action = _RULE_BASED_STRATEGY_MAP[error_code]
        return {
            "action": action,
            "reason": f"规则决策：错误码 '{error_code}' 命中策略 '{action}'",
            "confidence": 1.0,
            "decision_source": "rule_error_code",
        }

    # 2. 再查错误信息关键词
    lower_msg = str(error_message or "").lower()
    for keywords, action in _KEYWORD_STRATEGY_MAP:
        if any(kw.lower() in lower_msg for kw in keywords):
            matched_kw = next(kw for kw in keywords if kw.lower() in lower_msg)
            return {
                "action": action,
                "reason": f"规则决策：错误信息中含关键词 '{matched_kw}'，命中策略 '{action}'",
                "confidence": 0.85,
                "decision_source": "rule_keyword",
            }

    return None  # 规则无法判断，交给 AI


# =====================================================================
# AI 决策（调用 Gemini 分析失败截图 + 上下文）
# =====================================================================

def _load_decider_config(config_path: str = "config.json") -> dict:
    """读取策略决策器配置，合并用户配置和默认值。"""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return {**DECIDER_DEFAULTS, **config.get("strategy_decider", {})}
    except Exception:
        return dict(DECIDER_DEFAULTS)


def _get_gemini_client_for_decider(config: dict):
    """获取 Gemini 客户端（复用与 vision_agent 一致的获取逻辑）。"""
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("环境变量 GEMINI_API_KEY 未配置，无法使用策略决策器！")

    timeout = int(config.get("request_timeout_seconds", 15) or 15)
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=timeout,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    return client


def _build_decision_prompt(
    error_code: str,
    error_message: str,
    site_url: str,
    available_strategies: list[str],
) -> str:
    """构建发给 Gemini 的决策 Prompt。"""
    strategies_desc = {
        STRATEGY_RETRY_DOM: "retry_dom - 重新尝试传统 DOM 方式（适用于临时超时/弹窗/网络波动）",
        STRATEGY_TRY_VISION: "try_vision - 调用 Gemini Vision AI 截图分析（适用于 DOM 找不到但页面有评论区的情况）",
        STRATEGY_TRY_SSO: "try_sso - 尝试 Google SSO 单点登录（适用于检测到登录墙的情况）",
        STRATEGY_SKIP: "skip - 跳过本站点（适用于验证码保护、超时严重、证明真的没评论区）",
        STRATEGY_MARK_BLACKLIST: "mark_blacklist - 长期跳过（适用于评论关闭、明确no评论功能的站点）",
    }
    available_desc = "\n".join(
        f"  - {strategies_desc[s]}"
        for s in available_strategies
        if s in strategies_desc
    )

    return f"""
你是一个外链自动化系统的智能策略决策器。
刚刚有一个自动发布外链的任务失败了，请根据以下信息判断下一步最优策略。

【失败信息】
- 站点 URL: {site_url}
- 错误码: {error_code}
- 错误描述: {error_message}

【可选策略】
{available_desc}

请分析截图中的页面状态（如果有）和失败信息，选择最合适的策略。

严格返回 JSON，不要加任何解释：
{{
  "action": "<策略名称，必须是上面可选策略之一>",
  "reason": "<一句中文说明选择这个策略的原因>",
  "confidence": <0到1之间的置信度小数>,
  "page_observations": "<从截图中观察到的关键信息，如没有截图则填空字符串>"
}}
""".strip()


def _save_decision_log(
    debug_dir: Path,
    site_url: str,
    error_code: str,
    decision: dict,
    raw_response: str = "",
) -> None:
    """保存决策记录到调试目录，便于后续分析。"""
    try:
        debug_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = debug_dir / f"{today}_decisions.jsonl"
        raw_preview = (raw_response or "")[:500]
        record = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "site_url": site_url,
            "error_code": error_code,
            "decision": decision,
            "raw_response": raw_preview,
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 日志失败不影响主流程


def _ai_decide(
    error_code: str,
    error_message: str,
    site_url: str,
    screenshot_bytes: Optional[bytes],
    config: dict,
    config_path: str = "config.json",
) -> Optional[dict]:
    """
    调用 Gemini AI 分析失败截图和上下文，返回决策结果。
    失败时返回 None，上层会启用规则兜底。
    """
    try:
        client = _get_gemini_client_for_decider(config)
        from google.genai import types

        model = str(config.get("model", "gemini-2.0-flash") or "gemini-2.0-flash")

        # 根据是否有 SSO 配置决定可用策略
        available_strategies = [
            STRATEGY_RETRY_DOM,
            STRATEGY_TRY_VISION,
            STRATEGY_TRY_SSO,
            STRATEGY_SKIP,
            STRATEGY_MARK_BLACKLIST,
        ]

        prompt = _build_decision_prompt(error_code, error_message, site_url, available_strategies)

        # 构建请求内容：如果有截图就带上，没有则纯文本
        contents: list[Any] = []
        if screenshot_bytes and len(screenshot_bytes) > 1000:
            contents.append(types.Part.from_bytes(data=screenshot_bytes, mime_type="image/jpeg"))
        contents.append(prompt)

        response = client.models.generate_content(model=model, contents=contents)
        raw_text = (response.text or "").strip()

        # 解析 JSON 返回
        clean_text = re.sub(r"```(?:json)?\s*", "", raw_text).replace("```", "").strip()
        json_match = re.search(r"\{.*\}", clean_text, re.DOTALL)
        if not json_match:
            return None

        parsed = json.loads(json_match.group())
        action = str(parsed.get("action", "") or "").strip()

        # 校验 action 是合法策略名
        valid_actions = {
            STRATEGY_RETRY_DOM, STRATEGY_TRY_VISION,
            STRATEGY_TRY_SSO, STRATEGY_SKIP, STRATEGY_MARK_BLACKLIST,
        }
        if action not in valid_actions:
            return None

        return {
            "action": action,
            "reason": str(parsed.get("reason", "") or ""),
            "confidence": float(parsed.get("confidence", 0.7) or 0.7),
            "page_observations": str(parsed.get("page_observations", "") or ""),
            "decision_source": "ai",
            "raw_response": raw_text,
        }
    except Exception as exc:
        exc_preview = str(exc)[:80]
        print(f"  ⚠️ 策略决策器 AI 调用失败: {exc_preview}，将使用规则兜底")
        return None


# =====================================================================
# 对外主接口
# =====================================================================

def decide_next_strategy(
    error_code: str,
    error_message: str,
    site_url: str,
    screenshot_bytes: Optional[bytes] = None,
    config_path: str = "config.json",
) -> dict:
    """
    决策下一步策略的主入口函数。

    决策顺序：
      1. 读取配置；若决策器已关闭，直接返回默认策略
      2. 先走快速规则决策（零延迟）
      3. 规则无法判断时，调用 Gemini AI 分析截图
      4. AI 失败或置信度不足时，启用规则兜底

    参数：
      error_code       - 当前失败的错误码（如 "dom_not_found"）
      error_message    - 当前失败的详细错误描述
      site_url         - 正在处理的目标站点 URL
      screenshot_bytes - 页面截图字节流（可选，提供后 AI 能看到页面状态）
      config_path      - 配置文件路径（默认 "config.json"）

    返回：
      {
        "action": "try_vision",           # 策略名
        "reason": "...",                  # 决策理由
        "confidence": 0.9,               # 置信度 0~1
        "decision_source": "rule|ai",    # 决策来源
      }
    """
    config = _load_decider_config(config_path)
    debug_dir = Path(str(config.get("debug_dir", "artifacts/strategy_decider") or "artifacts/strategy_decider"))

    # 如果决策器被关闭，直接返回默认策略（不改变现有行为）
    if not config.get("enabled", True):
        return {
            "action": STRATEGY_TRY_VISION,
            "reason": "策略决策器已关闭，使用默认策略（Vision 优先）",
            "confidence": 1.0,
            "decision_source": "disabled_fallback",
        }

    print(f"  🧠 策略决策器启动：分析失败原因 [{error_code}]...")

    # ① 快速规则决策
    rule_decision = _rule_based_decide(error_code, error_message)
    if rule_decision:
        print(f"  ✅ 规则决策完成 → 策略: {rule_decision['action']} | {rule_decision['reason']}")
        _save_decision_log(debug_dir, site_url, error_code, rule_decision)
        return rule_decision

    # ② AI 决策（规则无法判断时）
    print(f"  🤖 规则无法判断，上升为 AI 决策（站点: {site_url[:60]}）...")
    ai_decision = _ai_decide(
        error_code=error_code,
        error_message=error_message,
        site_url=site_url,
        screenshot_bytes=screenshot_bytes,
        config=config,
        config_path=config_path,
    )

    confidence_threshold = float(config.get("confidence_threshold", 0.6) or 0.6)
    if ai_decision and ai_decision.get("confidence", 0) >= confidence_threshold:
        print(f"  ✅ AI 决策完成 → 策略: {ai_decision['action']} | {ai_decision['reason']}")
        _save_decision_log(debug_dir, site_url, error_code, ai_decision, ai_decision.get("raw_response", ""))
        # 移除 raw_response 避免被外部代码意外使用
        result: dict = {k: v for k, v in ai_decision.items() if k != "raw_response"}
        return result

    # ③ AI 失败或置信度不足 → 保守兜底（跳过，避免无效消耗）
    fallback = {
        "action": STRATEGY_SKIP,
        "reason": f"AI 决策置信度不足或失败（error_code={error_code}），保守跳过此次",
        "confidence": 0.5,
        "decision_source": "conservative_fallback",
    }
    print(f"  ⚠️ 保守兜底决策 → 策略: skip")
    _save_decision_log(debug_dir, site_url, error_code, fallback)
    return fallback


# =====================================================================
# 辅助工具：从决策结果中提取下一步动作布尔值（便于 form_automation 使用）
# =====================================================================

def should_try_vision(decision: dict) -> bool:
    """判断决策结果是否应该尝试 Vision。"""
    return decision.get("action") == STRATEGY_TRY_VISION


def should_try_sso(decision: dict) -> bool:
    """判断决策结果是否应该尝试 SSO 登录。"""
    return decision.get("action") == STRATEGY_TRY_SSO


def should_retry_dom(decision: dict) -> bool:
    """判断决策结果是否应该重试 DOM。"""
    return decision.get("action") == STRATEGY_RETRY_DOM


def should_skip(decision: dict) -> bool:
    """判断决策结果是否应该跳过。"""
    return decision.get("action") in {STRATEGY_SKIP, STRATEGY_MARK_BLACKLIST}


def is_blacklist(decision: dict) -> bool:
    """判断决策结果是否建议标记为长期黑名单。"""
    return decision.get("action") == STRATEGY_MARK_BLACKLIST
