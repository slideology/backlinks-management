from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from agent_memory import AgentMemory, normalize_failure_category
from legacy_feishu_history import (
    DEFAULT_PROMOTED_SITE_MAP,
    LegacyFeishuHistoryStore,
    extract_cell_text,
    extract_cell_url,
    get_root_domain,
    normalize_source_url,
)


DEFAULT_DAILY_SUCCESS_GOAL = 10
DEFAULT_COOLDOWN_DAYS = 1
DEFAULT_IN_PROGRESS_TIMEOUT_MINUTES = 15
DEFAULT_AGENT_ASSIST_FAILURE_THRESHOLD = 2
DEFAULT_SAME_DOMAIN_DAILY_LIMIT = 1
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"
LEGACY_SITE_KEY_ALIASES = {
    "b": "bearclicker.net",
    "n": "nanobananaimage.org",
}

STATUS_NOT_STARTED = "未开始"
STATUS_BLOCKED = "顺序阻塞"
STATUS_COOLDOWN = "冷却中"
STATUS_PENDING_RETRY = "待重试"
STATUS_IN_PROGRESS = "进行中"
STATUS_SUCCESS = "成功"
STATUS_SKIPPED = "跳过"
STATUS_ALL_DONE = "全部完成"
EXECUTION_MODE_CLASSIC = "classic_dom"
EXECUTION_MODE_AGENT = "agent_assisted"

TARGET_SITE_HEADERS = [
    "站点标识",
    "目标网站",
    "默认锚文本",
    "网站说明",
    "联系邮箱",
    "优先级",
    "冷却天数",
    "每日成功目标",
    "是否启用",
    "创建时间",
]

STATUS_HEADERS = [
    "来源链接",
    "来源标题",
    "根域名",
    "页面评分",
    "目标站标识",
    "状态",
    "最近成功时间",
    "最后尝试时间",
    "最近失败时间",
    "最近失败原因",
    "下次可发时间",
    "当前评论内容",
    "链接格式",
    "来源类型",
    "有网址字段",
    "有验证码",
    "执行模式",
    "推荐策略",
    "最近失败分类",
    "域名冷却至",
    "最后更新时间",
]

SOURCE_MASTER_BASE_HEADERS = [
    "来源标题",
    "来源链接",
    "根域名",
    "页面评分",
    "当前应发站点",
    "整体状态",
    "最近成功站点",
    "最近成功时间",
    "下次可推进时间",
    "最后失败原因",
    "最后失败分类",
    "最后更新时间",
    "当前执行模式",
    "推荐策略",
    "域名冷却至",
    "初始链接格式",
    "最终链接格式",
    "格式检测阶段",
    "格式检测证据",
    "格式检测置信度",
    "是否需要登录",
    "是否支持Google登录",
    "评论区是否存在",
    "历史外链验证结果",
    "历史审计状态",
    "格式检测状态",
]

LEGACY_HISTORY_HEADERS = [
    "来源标题",
    "来源链接",
    "根域名",
    "目标站标识",
    "成功时间",
    "来源标签页",
    "来源行号",
    "页面评分",
]

LEGACY_SOURCE_LIBRARY_HEADERS = [
    "来源标题",
    "来源链接",
    "根域名",
    "页面评分",
    "旧表标签页",
    "旧表分类",
    "旧表n标记",
    "旧表b标记",
    "旧表附加值",
    "旧表行号",
]

OLD_RECORD_HEADERS = [
    "来源标题",
    "来源链接",
    "根域名",
    "目标网站",
    "状态",
    "评论内容",
    "评论内容中文",
    "失败原因",
    "执行时间",
    "成功链接",
    "链接格式",
    "来源类型",
    "每日批次",
    "最后更新时间",
]


def now_text(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).strftime(DATETIME_FMT)


def parse_dt(value) -> Optional[datetime]:
    text = str(extract_cell_text(value) or "").strip()
    if not text:
        return None
    for fmt in (DATETIME_FMT, "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def iso_date(value) -> str:
    dt = parse_dt(value)
    return dt.strftime("%Y-%m-%d") if dt else ""


def _load_agent_assist_runtime(config_path: str = "config.json") -> dict:
    defaults = {
        "same_domain_daily_limit": DEFAULT_SAME_DOMAIN_DAILY_LIMIT,
    }
    try:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
        return {**defaults, **payload.get("agent_assist", {})}
    except Exception:
        return defaults


def _format_agent_dt(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.fromisoformat(text).strftime(DATETIME_FMT)
    except Exception:
        return text


def _has_complex_editor_hint(source_url: str, row: dict, profile: dict) -> bool:
    text = " | ".join(
        [
            str(source_url or "").lower(),
            str(row.get("最近失败原因", "") or "").lower(),
            str(row.get("链接格式", "") or "").lower(),
            str(profile.get("best_strategy", "") or "").lower(),
            str(profile.get("recent_failure_category", "") or "").lower(),
        ]
    )
    return any(
        marker in text
        for marker in (
            "blogger",
            "blogspot",
            "disqus",
            "iframe",
            "contenteditable",
            "prosemirror",
            "quill",
            "ckeditor",
            "draft",
            "textarea_not_found",
            "click_no_effect",
            "editor_complex",
        )
    )


def _recommended_strategy_for_row(row: dict, profile: dict) -> str:
    recent_category = normalize_failure_category(
        str(row.get("最近失败分类", "") or profile.get("recent_failure_category", "") or ""),
        str(row.get("最近失败原因", "") or profile.get("recent_failure_reason", "") or ""),
    )
    best_strategy = str(profile.get("best_strategy", "") or "dom").strip().lower()
    if best_strategy in {"dom", "vision", "sso"}:
        return best_strategy
    if recent_category == "editor_complex":
        return "iframe"
    if recent_category == "vision_unavailable":
        return "dom"
    if recent_category == "page_protected":
        return "skip"
    return "dom"


def _execution_mode_for_row(source_url: str, row: dict, profile: dict) -> str:
    recent_category = normalize_failure_category(
        str(row.get("最近失败分类", "") or profile.get("recent_failure_category", "") or ""),
        str(row.get("最近失败原因", "") or profile.get("recent_failure_reason", "") or ""),
    )
    if profile.get("blacklisted") or profile.get("temporarily_blacklisted"):
        return EXECUTION_MODE_AGENT
    if int(profile.get("consecutive_failures", 0) or 0) >= DEFAULT_AGENT_ASSIST_FAILURE_THRESHOLD:
        return EXECUTION_MODE_AGENT
    if recent_category in {
        "network_timeout",
        "page_protected",
        "comment_unavailable",
        "editor_complex",
        "vision_unavailable",
        "post_unverified",
    }:
        return EXECUTION_MODE_AGENT
    if row.get("状态") == STATUS_PENDING_RETRY:
        return EXECUTION_MODE_AGENT
    if _has_complex_editor_hint(source_url, row, profile):
        return EXECUTION_MODE_AGENT
    return EXECUTION_MODE_CLASSIC


def canonical_site_key(site_key: str = "", target_url: str = "", promoted_site_map: Optional[dict] = None) -> str:
    raw_site_key = str(site_key or "").strip().lower()
    mapping = promoted_site_map or DEFAULT_PROMOTED_SITE_MAP
    if raw_site_key:
        if raw_site_key in LEGACY_SITE_KEY_ALIASES:
            return LEGACY_SITE_KEY_ALIASES[raw_site_key]
        if "." in raw_site_key:
            return raw_site_key
        for mapped_value in mapping.values():
            normalized_value = str(mapped_value or "").strip().lower()
            if raw_site_key == normalized_value:
                return normalized_value

    domain = get_root_domain(target_url)
    if domain:
        mapped = str(mapping.get(domain, domain) or "").strip().lower()
        return LEGACY_SITE_KEY_ALIASES.get(mapped, mapped or domain)

    return raw_site_key


def site_key_for_target_url(target_url: str, promoted_site_map: Optional[dict] = None) -> str:
    return canonical_site_key(target_url=target_url, promoted_site_map=promoted_site_map)


def normalize_target_url(value) -> str:
    return normalize_source_url(extract_cell_url(value) or extract_cell_text(value))


def normalize_status_label(value: str) -> str:
    text = str(extract_cell_text(value) or "").strip()
    if not text:
        return STATUS_NOT_STARTED
    mapping = {
        "not_started": STATUS_NOT_STARTED,
        "pending": STATUS_NOT_STARTED,
        "待处理": STATUS_NOT_STARTED,
        "pending_retry": STATUS_PENDING_RETRY,
        "failed": STATUS_PENDING_RETRY,
        "失败": STATUS_PENDING_RETRY,
        "in_progress": STATUS_IN_PROGRESS,
        "进行中": STATUS_IN_PROGRESS,
        "completed": STATUS_SUCCESS,
        "success": STATUS_SUCCESS,
        "已完成": STATUS_SUCCESS,
        "成功": STATUS_SUCCESS,
        "skipped": STATUS_SKIPPED,
        "跳过": STATUS_SKIPPED,
        "blocked_by_order": STATUS_BLOCKED,
        "顺序阻塞": STATUS_BLOCKED,
        "cooldown": STATUS_COOLDOWN,
        "冷却中": STATUS_COOLDOWN,
    }
    return mapping.get(text, text)


def dynamic_source_headers(target_rows: list[dict]) -> list[str]:
    dynamic = []
    for target in sorted_target_rows(target_rows):
        site_key = str(target.get("站点标识", "") or "").strip()
        if not site_key:
            continue
        dynamic.extend(
            [
                f"{site_key}_状态",
                f"{site_key}_最近成功时间",
                f"{site_key}_最后尝试时间",
                f"{site_key}_下次可发时间",
                f"{site_key}_最后失败原因",
            ]
        )
    return SOURCE_MASTER_BASE_HEADERS + dynamic


def load_targets(config_path: str = "targets.json") -> list[dict]:
    path = Path(config_path)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("targets", [])
    except Exception:
        return []


def build_target_site_rows(
    existing_rows: Optional[list[dict]] = None,
    bootstrap_targets: Optional[list[dict]] = None,
    promoted_site_map: Optional[dict] = None,
    created_at: Optional[str] = None,
) -> list[dict]:
    reverse_map = {}
    for domain, key in (promoted_site_map or DEFAULT_PROMOTED_SITE_MAP).items():
        normalized_domain = domain.lower()
        if normalized_domain.startswith("www."):
            normalized_domain = normalized_domain[4:]
        reverse_map[normalized_domain] = canonical_site_key(
            site_key=key,
            target_url=f"https://{normalized_domain}/",
            promoted_site_map=promoted_site_map,
        )

    created_time = created_at or now_text()
    by_key = {}

    for row in existing_rows or []:
        target_url = normalize_target_url(row.get("目标网站", ""))
        site_key = canonical_site_key(
            site_key=str(extract_cell_text(row.get("站点标识", "")) or "").strip(),
            target_url=target_url,
            promoted_site_map=promoted_site_map,
        )
        if not site_key and not target_url:
            continue
        priority = str(extract_cell_text(row.get("优先级", "")) or "").strip()
        by_key[site_key or target_url] = {
            "站点标识": site_key,
            "目标网站": target_url,
            "默认锚文本": str(extract_cell_text(row.get("默认锚文本", "")) or "").strip(),
            "网站说明": str(extract_cell_text(row.get("网站说明", "")) or "").strip(),
            "联系邮箱": str(extract_cell_text(row.get("联系邮箱", "")) or "").strip(),
            "优先级": priority or str(len(by_key) + 1),
            "冷却天数": str(extract_cell_text(row.get("冷却天数", "")) or DEFAULT_COOLDOWN_DAYS),
            "每日成功目标": str(extract_cell_text(row.get("每日成功目标", "")) or DEFAULT_DAILY_SUCCESS_GOAL),
            "是否启用": "是" if str(extract_cell_text(row.get("是否启用", "")) or "").strip() == "是" else "否",
            "创建时间": str(extract_cell_text(row.get("创建时间", "")) or created_time),
        }

    for target in bootstrap_targets or []:
        target_url = normalize_source_url(str(target.get("url", "") or ""))
        if not target_url:
            continue
        domain = get_root_domain(target_url)
        site_key = reverse_map.get(domain, canonical_site_key(target_url=target_url, promoted_site_map=promoted_site_map))
        row_key = site_key or target_url
        current = by_key.get(row_key, {})
        by_key[row_key] = {
            "站点标识": current.get("站点标识", site_key),
            "目标网站": current.get("目标网站", target_url),
            "默认锚文本": current.get("默认锚文本") or str(target.get("anchor_text", "") or ""),
            "网站说明": current.get("网站说明") or str(target.get("description", "") or ""),
            "联系邮箱": current.get("联系邮箱") or str(target.get("email", "") or ""),
            "优先级": current.get("优先级") or str(len(by_key)),
            "冷却天数": current.get("冷却天数") or str(DEFAULT_COOLDOWN_DAYS),
            "每日成功目标": current.get("每日成功目标") or str(DEFAULT_DAILY_SUCCESS_GOAL),
            "是否启用": current.get("是否启用") or ("是" if target.get("active") else "否"),
            "创建时间": current.get("创建时间") or created_time,
        }

    for domain, site_key in sorted(reverse_map.items(), key=lambda item: item[1]):
        by_key.setdefault(
            site_key,
            {
                "站点标识": site_key,
                "目标网站": f"https://{domain}/",
                "默认锚文本": "",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": str(len(by_key) + 1),
                "冷却天数": str(DEFAULT_COOLDOWN_DAYS),
                "每日成功目标": str(DEFAULT_DAILY_SUCCESS_GOAL),
                "是否启用": "否",
                "创建时间": created_time,
            },
        )

    rows = list(by_key.values())
    rows.sort(key=lambda item: (_safe_int(item.get("优先级", 9999)), item.get("站点标识", "")))
    for idx, row in enumerate(rows, start=1):
        if not str(row.get("优先级", "")).strip():
            row["优先级"] = str(idx)
    return rows


def sorted_target_rows(target_rows: list[dict], active_only: bool = False) -> list[dict]:
    rows = []
    for row in target_rows:
        if active_only and str(row.get("是否启用", "") or "") != "是":
            continue
        rows.append(row)
    rows.sort(key=lambda item: (_safe_int(item.get("优先级", 9999)), item.get("站点标识", "")))
    return rows


def target_runtime_payload(target_row: dict) -> dict:
    return {
        "site_key": canonical_site_key(
            site_key=str(target_row.get("站点标识", "") or ""),
            target_url=normalize_target_url(target_row.get("目标网站", "")),
        ),
        "url": normalize_target_url(target_row.get("目标网站", "")),
        "anchor_text": str(extract_cell_text(target_row.get("默认锚文本", "")) or "").strip(),
        "description": str(extract_cell_text(target_row.get("网站说明", "")) or "").strip(),
        "email": str(extract_cell_text(target_row.get("联系邮箱", "")) or "").strip(),
        "daily_success_goal": _safe_int(target_row.get("每日成功目标", DEFAULT_DAILY_SUCCESS_GOAL)),
        "cooldown_days": _safe_int(target_row.get("冷却天数", DEFAULT_COOLDOWN_DAYS)),
        "priority": _safe_int(target_row.get("优先级", 9999)),
    }


def build_legacy_history_rows(history_store: Optional[LegacyFeishuHistoryStore]) -> list[dict]:
    if not history_store:
        return []
    rows = []
    for record in history_store.records:
        site_key = canonical_site_key(
            site_key=record.promoted_site_key,
            promoted_site_map=getattr(history_store, "promoted_site_map", None),
        )
        rows.append(
            {
                "来源标题": record.source_title,
                "来源链接": record.source_url,
                "根域名": record.source_root_domain,
                "目标站标识": site_key,
                "成功时间": record.posted_date_raw,
                "来源标签页": record.legacy_tab_title,
                "来源行号": str(record.source_row),
                "页面评分": record.page_ascore,
            }
        )
    rows.sort(key=lambda row: (row["目标站标识"], row["根域名"], row["来源链接"]))
    return rows


def build_legacy_source_library_rows(history_store: Optional[LegacyFeishuHistoryStore]) -> list[dict]:
    if not history_store:
        return []

    best_by_domain = {}
    for source_row in history_store.source_rows:
        current = best_by_domain.get(source_row.source_root_domain)
        current_score = float(current.page_ascore) if current and str(current.page_ascore).strip() else -1
        candidate_score = float(source_row.page_ascore) if str(source_row.page_ascore).strip() else -1
        candidate_has_marker = bool(str(source_row.n_marker).strip() or str(source_row.b_marker).strip())
        current_has_marker = bool(current and (str(current.n_marker).strip() or str(current.b_marker).strip()))

        should_replace = False
        if current is None:
            should_replace = True
        elif candidate_score > current_score:
            should_replace = True
        elif candidate_score == current_score and candidate_has_marker and not current_has_marker:
            should_replace = True
        elif candidate_score == current_score and candidate_has_marker == current_has_marker:
            should_replace = (source_row.source_url, source_row.source_row) < (current.source_url, current.source_row)

        if should_replace:
            best_by_domain[source_row.source_root_domain] = source_row

    rows = []
    for source_row in best_by_domain.values():
        rows.append(
            {
                "来源标题": source_row.source_title,
                "来源链接": source_row.source_url,
                "根域名": source_row.source_root_domain,
                "页面评分": source_row.page_ascore,
                "旧表标签页": source_row.legacy_tab_title,
                "旧表分类": source_row.tab_category,
                "旧表n标记": source_row.n_marker,
                "旧表b标记": source_row.b_marker,
                "旧表附加值": source_row.extra_value,
                "旧表行号": str(source_row.source_row),
            }
        )

    rows.sort(key=lambda row: (-_safe_float(row.get("页面评分", 0)), row["根域名"], row["来源链接"]))
    return rows


def migrate_old_record_rows(old_rows: list[dict], target_rows: list[dict], promoted_site_map: Optional[dict] = None) -> list[dict]:
    if not old_rows:
        return []

    target_by_url = {}
    target_by_key = {}
    for target in target_rows:
        payload = target_runtime_payload(target)
        target_by_url[payload["url"]] = payload
        target_by_key[payload["site_key"]] = payload

    latest_by_pair = {}
    for row in old_rows:
        source_url = normalize_source_url(extract_cell_url(row.get("来源链接", "")) or extract_cell_text(row.get("来源链接", "")))
        target_url = normalize_source_url(extract_cell_url(row.get("目标网站", "")) or extract_cell_text(row.get("目标网站", "")))
        if not source_url or not target_url:
            continue

        site_key = canonical_site_key(
            site_key=target_by_url.get(target_url, {}).get("site_key", ""),
            target_url=target_url,
            promoted_site_map=promoted_site_map,
        )
        if not site_key:
            continue

        candidate = {
            "来源链接": source_url,
            "来源标题": str(extract_cell_text(row.get("来源标题", "")) or "").strip(),
            "根域名": get_root_domain(source_url),
            "页面评分": "",
            "目标站标识": site_key,
            "状态": normalize_status_label(row.get("状态", "")),
            "最近成功时间": str(extract_cell_text(row.get("执行时间", "")) or "").strip()
            if normalize_status_label(row.get("状态", "")) == STATUS_SUCCESS
            else "",
            "最后尝试时间": str(extract_cell_text(row.get("执行时间", "")) or "").strip(),
            "最近失败时间": str(extract_cell_text(row.get("执行时间", "")) or "").strip()
            if normalize_status_label(row.get("状态", "")) == STATUS_PENDING_RETRY
            else "",
            "最近失败原因": str(extract_cell_text(row.get("失败原因", "")) or "").strip(),
            "下次可发时间": "",
            "当前评论内容": str(extract_cell_text(row.get("评论内容", "")) or "").strip(),
            "链接格式": str(extract_cell_text(row.get("链接格式", "")) or "").strip(),
            "来源类型": str(extract_cell_text(row.get("来源类型", "")) or "").strip(),
            "有网址字段": "",
            "有验证码": "",
            "执行模式": EXECUTION_MODE_CLASSIC,
            "推荐策略": "dom",
            "最近失败分类": normalize_failure_category(
                normalize_status_label(row.get("状态", "")),
                str(extract_cell_text(row.get("失败原因", "")) or "").strip(),
            ) if normalize_status_label(row.get("状态", "")) == STATUS_PENDING_RETRY else "",
            "域名冷却至": "",
            "最后更新时间": str(extract_cell_text(row.get("最后更新时间", "")) or "").strip(),
        }
        pair_key = (source_url, site_key)
        existing = latest_by_pair.get(pair_key)
        if not existing or _sort_time_key(candidate) >= _sort_time_key(existing):
            latest_by_pair[pair_key] = candidate

    return list(latest_by_pair.values())


def reconcile_status_rows(
    existing_status_rows: list[dict],
    target_rows: list[dict],
    library_rows: list[dict],
    legacy_history_rows: list[dict],
    promoted_site_map: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> list[dict]:
    current_time = now or datetime.now()
    target_order = sorted_target_rows(target_rows)
    memory = AgentMemory()
    status_map = {}
    for row in existing_status_rows:
        source_url = normalize_source_url(extract_cell_url(row.get("来源链接", "")) or extract_cell_text(row.get("来源链接", "")))
        site_key = canonical_site_key(
            site_key=str(extract_cell_text(row.get("目标站标识", "")) or "").strip(),
            target_url="",
            promoted_site_map=promoted_site_map,
        )
        if source_url and site_key:
            status_map[(source_url, site_key)] = {
                header: str(extract_cell_text(row.get(header, "")) or "").strip()
                for header in STATUS_HEADERS
            }

    history_map = {}
    for row in legacy_history_rows:
        source_url = normalize_source_url(extract_cell_url(row.get("来源链接", "")) or extract_cell_text(row.get("来源链接", "")))
        site_key = canonical_site_key(
            site_key=str(extract_cell_text(row.get("目标站标识", "")) or "").strip(),
            promoted_site_map=promoted_site_map,
        )
        if source_url and site_key:
            history_map[(source_url, site_key)] = {**row, "目标站标识": site_key}

    source_catalog = {}
    for row in library_rows:
        source_url = normalize_source_url(extract_cell_url(row.get("来源链接", "")) or extract_cell_text(row.get("来源链接", "")))
        if not source_url:
            continue
        source_catalog[source_url] = {
            "来源标题": str(extract_cell_text(row.get("来源标题", "")) or "").strip(),
            "来源链接": source_url,
            "根域名": get_root_domain(source_url),
            "页面评分": str(extract_cell_text(row.get("页面评分", "")) or "").strip(),
        }
    for row in existing_status_rows + legacy_history_rows:
        source_url = normalize_source_url(extract_cell_url(row.get("来源链接", "")) or extract_cell_text(row.get("来源链接", "")))
        if not source_url:
            continue
        source_catalog.setdefault(
            source_url,
            {
                "来源标题": str(extract_cell_text(row.get("来源标题", "")) or "").strip(),
                "来源链接": source_url,
                "根域名": get_root_domain(source_url),
                "页面评分": str(extract_cell_text(row.get("页面评分", "")) or "").strip(),
            },
        )

    rows = []
    for source_url, source_info in source_catalog.items():
        source_existing_rows = [
            item
            for (row_url, _site_key), item in status_map.items()
            if row_url == source_url
        ]
        source_history_rows = [
            item
            for (row_url, _site_key), item in history_map.items()
            if row_url == source_url
        ]

        def _latest_other_success_dt(current_site_key: str) -> Optional[datetime]:
            latest: Optional[datetime] = None
            for item in source_existing_rows:
                if canonical_site_key(item.get("目标站标识", ""), "") == current_site_key:
                    continue
                status = normalize_status_label(item.get("状态", ""))
                success_dt = parse_dt(item.get("最近成功时间", ""))
                if status == STATUS_SUCCESS and success_dt and (latest is None or success_dt > latest):
                    latest = success_dt
            for item in source_history_rows:
                if canonical_site_key(item.get("目标站标识", "")) == current_site_key:
                    continue
                success_dt = parse_dt(item.get("成功时间", ""))
                if success_dt and (latest is None or success_dt > latest):
                    latest = success_dt
            return latest

        for target in target_order:
            runtime_target = target_runtime_payload(target)
            site_key = runtime_target["site_key"]
            if not site_key:
                continue
            current = status_map.get((source_url, site_key), {}).copy()
            history = history_map.get((source_url, site_key))
            success_time = current.get("最近成功时间", "")
            if not success_time and history:
                success_time = str(extract_cell_text(history.get("成功时间", "")) or "").strip()
            has_success_fact = bool(success_time) or bool(history) or normalize_status_label(current.get("状态", "")) == STATUS_SUCCESS

            row = {
                "来源链接": source_url,
                "来源标题": current.get("来源标题") or source_info.get("来源标题", ""),
                "根域名": source_info.get("根域名") or current.get("根域名", ""),
                "页面评分": source_info.get("页面评分") or current.get("页面评分", ""),
                "目标站标识": site_key,
                "状态": normalize_status_label(current.get("状态", "")),
                "最近成功时间": success_time,
                "最后尝试时间": current.get("最后尝试时间", ""),
                "最近失败时间": current.get("最近失败时间", ""),
                "最近失败原因": current.get("最近失败原因", ""),
                "下次可发时间": current.get("下次可发时间", ""),
                "当前评论内容": current.get("当前评论内容", ""),
                "链接格式": current.get("链接格式", ""),
                "来源类型": current.get("来源类型", ""),
                "有网址字段": current.get("有网址字段", ""),
                "有验证码": current.get("有验证码", ""),
                "执行模式": current.get("执行模式", ""),
                "推荐策略": current.get("推荐策略", ""),
                "最近失败分类": current.get("最近失败分类", ""),
                "域名冷却至": current.get("域名冷却至", ""),
                "最后更新时间": current.get("最后更新时间", ""),
            }

            status = normalize_status_label(row["状态"])
            if status == STATUS_IN_PROGRESS:
                last_activity = parse_dt(row.get("最后更新时间", "")) or parse_dt(row.get("最后尝试时间", ""))
                attempt_day = iso_date(row.get("最后尝试时间", ""))
                if last_activity and current_time - last_activity >= timedelta(minutes=DEFAULT_IN_PROGRESS_TIMEOUT_MINUTES):
                    status = STATUS_PENDING_RETRY
                elif attempt_day and attempt_day != current_time.strftime("%Y-%m-%d"):
                    status = STATUS_PENDING_RETRY

            if has_success_fact:
                row["状态"] = STATUS_SUCCESS
                row["下次可发时间"] = ""
                prior_complete = True
                prior_success_time = parse_dt(success_time)
            elif status == STATUS_SKIPPED:
                row["状态"] = STATUS_SKIPPED
            else:
                latest_other_success = _latest_other_success_dt(site_key)
                if latest_other_success:
                    cooldown_days = runtime_target["cooldown_days"] or DEFAULT_COOLDOWN_DAYS
                    row["下次可发时间"] = (latest_other_success + timedelta(days=cooldown_days)).strftime(DATETIME_FMT)
                next_allowed = parse_dt(row.get("下次可发时间", ""))
                if next_allowed and next_allowed > current_time:
                    row["状态"] = STATUS_COOLDOWN
                elif status == STATUS_PENDING_RETRY:
                    row["状态"] = STATUS_PENDING_RETRY
                elif status == STATUS_IN_PROGRESS:
                    row["状态"] = STATUS_IN_PROGRESS
                else:
                    row["状态"] = STATUS_NOT_STARTED

            profile = memory.get_site_profile(source_url)
            row["最近失败分类"] = (
                normalize_failure_category(
                    row.get("最近失败分类", "") or profile.get("recent_failure_category", ""),
                    row.get("最近失败原因", "") or profile.get("recent_failure_reason", ""),
                )
                if row.get("最近失败原因", "") or profile.get("recent_failure_category", "")
                else ""
            )
            row["推荐策略"] = row.get("推荐策略", "") or _recommended_strategy_for_row(row, profile)
            row["执行模式"] = _execution_mode_for_row(source_url, row, profile)
            row["域名冷却至"] = _format_agent_dt(profile.get("cooldown_until", "")) or row.get("域名冷却至", "")

            rows.append(row)

    rows.sort(key=lambda item: (item["来源链接"], _safe_int(_target_priority(target_rows, item["目标站标识"]), 9999)))
    return rows


def build_source_master_rows(
    status_rows: list[dict],
    target_rows: list[dict],
    existing_source_rows: Optional[list[dict]] = None,
) -> list[dict]:
    grouped = defaultdict(list)
    target_by_key = {str(row.get("站点标识", "")): row for row in sorted_target_rows(target_rows)}
    ordered_targets = sorted_target_rows(target_rows)
    existing_by_url = {}
    for row in existing_source_rows or []:
        normalized_url = normalize_source_url(extract_cell_url(row.get("来源链接", "")) or extract_cell_text(row.get("来源链接", "")))
        if normalized_url:
            existing_by_url[normalized_url] = row
    for row in status_rows:
        grouped[row["来源链接"]].append(row)

    result = []
    for source_url, rows in grouped.items():
        row_by_key = {row["目标站标识"]: row for row in rows}
        latest_update = max((row.get("最后更新时间", "") for row in rows), default="")
        all_done = True
        current_row = None
        latest_success_row = None
        latest_failure_reason = ""
        latest_failure_category = ""
        next_progress_at = ""
        for target in ordered_targets:
            site_key = str(target.get("站点标识", "") or "")
            row = row_by_key.get(site_key)
            if not row:
                continue
            if row.get("最近失败原因") and not latest_failure_reason:
                latest_failure_reason = row["最近失败原因"]
            if row.get("最近失败分类") and not latest_failure_category:
                latest_failure_category = row["最近失败分类"]
            success_dt = parse_dt(row.get("最近成功时间", ""))
            if success_dt and (not latest_success_row or success_dt > parse_dt(latest_success_row.get("最近成功时间", ""))):
                latest_success_row = row
            if row["状态"] not in {STATUS_SUCCESS, STATUS_SKIPPED} and current_row is None:
                current_row = row
                next_progress_at = row.get("下次可发时间", "")
            if row["状态"] not in {STATUS_SUCCESS, STATUS_SKIPPED}:
                all_done = False

        anchor_row = rows[0]
        existing_probe = existing_by_url.get(source_url, {})
        summary = {
            "来源标题": anchor_row.get("来源标题", ""),
            "来源链接": source_url,
            "根域名": anchor_row.get("根域名", ""),
            "页面评分": anchor_row.get("页面评分", ""),
            "当前应发站点": current_row.get("目标站标识", "") if current_row else "",
            "整体状态": STATUS_ALL_DONE if all_done else (current_row.get("状态", STATUS_NOT_STARTED) if current_row else STATUS_NOT_STARTED),
            "最近成功站点": latest_success_row.get("目标站标识", "") if latest_success_row else "",
            "最近成功时间": latest_success_row.get("最近成功时间", "") if latest_success_row else "",
            "下次可推进时间": next_progress_at,
            "最后失败原因": latest_failure_reason,
            "最后失败分类": latest_failure_category,
            "最后更新时间": latest_update,
            "当前执行模式": current_row.get("执行模式", "") if current_row else "",
            "推荐策略": current_row.get("推荐策略", "") if current_row else "",
            "域名冷却至": current_row.get("域名冷却至", "") if current_row else "",
            "初始链接格式": str(extract_cell_text(existing_probe.get("初始链接格式", "")) or ""),
            "最终链接格式": str(extract_cell_text(existing_probe.get("最终链接格式", "")) or ""),
            "格式检测阶段": str(extract_cell_text(existing_probe.get("格式检测阶段", "")) or ""),
            "格式检测证据": str(extract_cell_text(existing_probe.get("格式检测证据", "")) or ""),
            "格式检测置信度": str(extract_cell_text(existing_probe.get("格式检测置信度", "")) or ""),
            "是否需要登录": str(extract_cell_text(existing_probe.get("是否需要登录", "")) or ""),
            "是否支持Google登录": str(extract_cell_text(existing_probe.get("是否支持Google登录", "")) or ""),
            "评论区是否存在": str(extract_cell_text(existing_probe.get("评论区是否存在", "")) or ""),
            "历史外链验证结果": str(extract_cell_text(existing_probe.get("历史外链验证结果", "")) or ""),
            "历史审计状态": str(extract_cell_text(existing_probe.get("历史审计状态", "")) or ""),
            "格式检测状态": str(extract_cell_text(existing_probe.get("格式检测状态", "")) or ""),
        }
        for target in ordered_targets:
            site_key = str(target.get("站点标识", "") or "")
            row = row_by_key.get(site_key, {})
            summary[f"{site_key}_状态"] = row.get("状态", STATUS_NOT_STARTED)
            summary[f"{site_key}_最近成功时间"] = row.get("最近成功时间", "")
            summary[f"{site_key}_最后尝试时间"] = row.get("最后尝试时间", "")
            summary[f"{site_key}_下次可发时间"] = row.get("下次可发时间", "")
            summary[f"{site_key}_最后失败原因"] = row.get("最近失败原因", "")
        result.append(summary)

    result.sort(key=lambda item: (item["整体状态"] != STATUS_ALL_DONE, item["根域名"], item["来源链接"]))
    return result


def select_daily_tasks(status_rows: list[dict], target_rows: list[dict], now: Optional[datetime] = None) -> tuple[list[dict], list[dict], dict]:
    current_time = now or datetime.now()
    today = current_time.strftime("%Y-%m-%d")
    assist_cfg = _load_agent_assist_runtime()
    same_domain_daily_limit = max(0, int(assist_cfg.get("same_domain_daily_limit", DEFAULT_SAME_DOMAIN_DAILY_LIMIT) or 0))
    by_source = defaultdict(list)
    for row in status_rows:
        by_source[row["来源链接"]].append(row)

    selected_keys = set()
    selected_domains = set()
    selected = []
    success_counts = defaultdict(int)
    attempted_domains_today = set()
    for row in status_rows:
        if row["状态"] == STATUS_SUCCESS and iso_date(row.get("最近成功时间", "")) == today:
            success_counts[row["目标站标识"]] += 1
        if same_domain_daily_limit > 0 and iso_date(row.get("最后尝试时间", "")) == today and row.get("根域名", ""):
            attempted_domains_today.add(str(row.get("根域名", "")).strip().lower())

    for target in sorted_target_rows(target_rows, active_only=True):
        payload = target_runtime_payload(target)
        site_key = payload["site_key"]
        remaining = max(0, payload["daily_success_goal"] - success_counts[site_key])
        if remaining <= 0:
            continue

        prioritized_not_started = []
        fresh_not_started = []
        prioritized_retry = []
        fresh_retry = []
        for row in status_rows:
            if row["目标站标识"] != site_key or row["状态"] not in {STATUS_NOT_STARTED, STATUS_PENDING_RETRY}:
                continue
            cooldown_until = parse_dt(row.get("域名冷却至", ""))
            if cooldown_until and cooldown_until > current_time:
                continue
            if (
                row.get("执行模式") == EXECUTION_MODE_AGENT
                and iso_date(row.get("最后尝试时间", "")) == today
            ):
                continue
            siblings = by_source[row["来源链接"]]
            other_success = any(
                sibling["目标站标识"] != site_key and sibling["状态"] == STATUS_SUCCESS
                for sibling in siblings
            )
            any_success = any(sibling["状态"] == STATUS_SUCCESS for sibling in siblings)
            if row["状态"] == STATUS_NOT_STARTED:
                bucket = prioritized_not_started if other_success else fresh_not_started if not any_success else None
            else:
                bucket = prioritized_retry if other_success else fresh_retry if not any_success else None
            if bucket is None:
                continue
            bucket.append(row)

        prioritized_not_started.sort(key=_candidate_sort_key)
        fresh_not_started.sort(key=_candidate_sort_key)
        prioritized_retry.sort(key=_candidate_sort_key)
        fresh_retry.sort(key=_candidate_sort_key)
        candidate_rows = (
            prioritized_not_started
            + fresh_not_started
            + prioritized_retry
            + fresh_retry
        )
        for row in candidate_rows:
            pair_key = (row["来源链接"], row["目标站标识"])
            root_domain = str(row.get("根域名", "")).strip().lower()
            if pair_key in selected_keys:
                continue
            if same_domain_daily_limit > 0 and root_domain and (root_domain in attempted_domains_today or root_domain in selected_domains):
                continue
            selected_keys.add(pair_key)
            if same_domain_daily_limit > 0 and root_domain:
                selected_domains.add(root_domain)
            row["状态"] = STATUS_IN_PROGRESS
            row["最后更新时间"] = current_time.strftime(DATETIME_FMT)
            selected.append(
                {
                    "status_row": row,
                    "target": payload,
                }
            )
            remaining -= 1
            if remaining <= 0:
                break

    meta = {
        "today_success_by_site": dict(success_counts),
        "selected_count": len(selected),
    }
    return selected, status_rows, meta


def source_urls_for_runtime(status_rows: list[dict]) -> set[str]:
    return {row["来源链接"] for row in status_rows if row.get("来源链接")}


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(str(extract_cell_text(value) or "").strip())
    except Exception:
        return default


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(str(extract_cell_text(value) or "").strip())
    except Exception:
        return default


def _sort_time_key(row: dict) -> tuple:
    return (
        parse_dt(row.get("最后更新时间", "")) or parse_dt(row.get("最后尝试时间", "")) or datetime.min,
        parse_dt(row.get("最近成功时间", "")) or datetime.min,
    )


def _candidate_sort_key(row: dict) -> tuple:
    return (
        0 if row["状态"] == STATUS_NOT_STARTED else 1,
        -_safe_float(row.get("页面评分", 0)),
        row.get("根域名", ""),
        row.get("来源链接", ""),
    )


def _target_priority(target_rows: list[dict], site_key: str):
    for row in target_rows:
        if str(row.get("站点标识", "")) == site_key:
            return row.get("优先级", 9999)
    return 9999
