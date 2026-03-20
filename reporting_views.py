import json
from collections import defaultdict
from pathlib import Path
from typing import Optional

from legacy_feishu_history import DEFAULT_PROMOTED_SITE_MAP, LegacyFeishuHistoryStore, get_root_domain, normalize_source_url
from sheet_localization import localize_basic_value


SOURCE_SUMMARY_HEADERS = [
    "来源标题",
    "来源链接",
    "根域名",
    "页面评分",
    "来源类型",
    "链接格式",
    "是否有网址字段",
    "是否有验证码",
    "最新状态",
    "最新目标网站",
    "最新评论内容",
    "最新评论内容中文",
    "最新失败原因",
    "最新执行时间",
    "成功次数",
    "失败次数",
    "已发目标站数量",
    "已发目标站列表",
]

POSTING_RECORD_HEADERS = [
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

TARGET_SITE_HEADERS = [
    "站点标识",
    "目标网站",
    "默认锚文本",
    "网站说明",
    "是否启用",
]

LEGACY_HISTORY_HEADERS = [
    "来源标题",
    "来源链接",
    "根域名",
    "目标站标识",
    "是否已发",
    "历史发布时间",
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


def load_targets(config_path: str = "targets.json") -> list[dict]:
    path = Path(config_path)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return payload.get("targets", [])


def _reverse_promoted_site_map(promoted_site_map: Optional[dict] = None) -> dict:
    mapping = promoted_site_map or DEFAULT_PROMOTED_SITE_MAP
    reversed_map = {}
    for domain, key in mapping.items():
        normalized_domain = domain.lower()
        if normalized_domain.startswith("www."):
            normalized_domain = normalized_domain[4:]
        reversed_map.setdefault(key, normalized_domain)
    return reversed_map


def _legacy_record_lookup(history_store: Optional[LegacyFeishuHistoryStore]) -> tuple[dict, dict]:
    if not history_store:
        return {}, {}

    source_lookup = {}
    posted_lookup = defaultdict(set)
    reverse_map = _reverse_promoted_site_map(history_store.promoted_site_map)
    for record in history_store.records:
        source_lookup.setdefault(record.source_url, record)
        canonical_target = reverse_map.get(record.promoted_site_key, record.promoted_site_key)
        posted_lookup[record.source_url].add(canonical_target)
    return source_lookup, posted_lookup


def build_source_summary_rows(rows: list[list[str]], headers: list[str], history_store: Optional[LegacyFeishuHistoryStore] = None) -> list[list[str]]:
    source_lookup, posted_lookup = _legacy_record_lookup(history_store)
    grouped = defaultdict(list)
    for row in rows[1:]:
        row_dict = {header: row[idx] if len(row) > idx else "" for idx, header in enumerate(headers)}
        source_url = normalize_source_url(str(row_dict.get("URL", "") or ""))
        if not source_url:
            continue
        grouped[source_url].append(row_dict)

    result_rows = []
    for source_url, grouped_rows in grouped.items():
        latest_row = max(
            grouped_rows,
            key=lambda item: (
                str(item.get("Execution_Date", "") or ""),
                str(item.get("Last_Updated", "") or ""),
            ),
        )
        history_record = source_lookup.get(source_url)
        posted_targets = set(posted_lookup.get(source_url, set()))
        for item in grouped_rows:
            if str(item.get("Status", "") or "") == "completed" and item.get("Target_Website"):
                posted_targets.add(str(item["Target_Website"]).strip())

        success_count = sum(1 for item in grouped_rows if str(item.get("Status", "") or "") == "completed")
        failure_count = sum(1 for item in grouped_rows if str(item.get("Status", "") or "") == "failed")
        notes = str(latest_row.get("Notes", "") or "")
        latest_failure_reason = notes if str(latest_row.get("Status", "") or "") == "failed" else ""

        result_rows.append(
            [
                history_record.source_title if history_record else "",
                source_url,
                get_root_domain(source_url),
                history_record.page_ascore if history_record else "",
                localize_basic_value("Type", str(latest_row.get("Type", "") or "")),
                localize_basic_value("Link_Format", str(latest_row.get("Link_Format", "") or "")),
                localize_basic_value("Has_URL_Field", str(latest_row.get("Has_URL_Field", "") or "")),
                localize_basic_value("Has_Captcha", str(latest_row.get("Has_Captcha", "") or "")),
                localize_basic_value("Status", str(latest_row.get("Status", "") or "")),
                str(latest_row.get("Target_Website", "") or ""),
                str(latest_row.get("Comment_Content", "") or ""),
                str(latest_row.get("Comment_Content_ZH", "") or ""),
                latest_failure_reason,
                str(latest_row.get("Execution_Date", "") or ""),
                str(success_count),
                str(failure_count),
                str(len(posted_targets)),
                ", ".join(sorted(posted_targets)),
            ]
        )

    result_rows.sort(key=lambda row: (row[8] != "已完成", row[2], row[1]))
    return result_rows


def build_posting_record_rows(rows: list[list[str]], headers: list[str], history_store: Optional[LegacyFeishuHistoryStore] = None) -> list[list[str]]:
    source_lookup, _ = _legacy_record_lookup(history_store)
    result_rows = []
    for row in rows[1:]:
        row_dict = {header: row[idx] if len(row) > idx else "" for idx, header in enumerate(headers)}
        source_url = normalize_source_url(str(row_dict.get("URL", "") or ""))
        if not source_url:
            continue

        history_record = source_lookup.get(source_url)
        status = str(row_dict.get("Status", "") or "")
        notes = str(row_dict.get("Notes", "") or "")
        failure_reason = notes if status == "failed" else ""

        result_rows.append(
            [
                history_record.source_title if history_record else "",
                source_url,
                get_root_domain(source_url),
                str(row_dict.get("Target_Website", "") or ""),
                localize_basic_value("Status", status),
                str(row_dict.get("Comment_Content", "") or ""),
                str(row_dict.get("Comment_Content_ZH", "") or ""),
                failure_reason,
                str(row_dict.get("Execution_Date", "") or ""),
                str(row_dict.get("Success_URL", "") or ""),
                localize_basic_value("Link_Format", str(row_dict.get("Link_Format", "") or "")),
                localize_basic_value("Type", str(row_dict.get("Type", "") or "")),
                localize_basic_value("Daily_Batch", str(row_dict.get("Daily_Batch", "") or "")),
                str(row_dict.get("Last_Updated", "") or ""),
            ]
        )

    result_rows.sort(key=lambda row: (row[7], row[1], row[3]))
    return result_rows


def build_target_site_rows(targets: list[dict], promoted_site_map: Optional[dict] = None) -> list[list[str]]:
    rows = []
    reverse_map = _reverse_promoted_site_map(promoted_site_map)
    explicit_targets = set()

    for target in targets:
        normalized_url = normalize_source_url(str(target.get("url", "") or ""))
        if not normalized_url:
            continue
        explicit_targets.add(get_root_domain(normalized_url))
        site_key = ""
        for candidate_key, domain in reverse_map.items():
            if domain == get_root_domain(normalized_url):
                site_key = candidate_key
                break
        rows.append(
            [
                site_key,
                normalized_url,
                str(target.get("anchor_text", "") or ""),
                str(target.get("description", "") or ""),
                "是" if target.get("active") else "否",
            ]
        )

    for site_key, domain in sorted(reverse_map.items()):
        if domain in explicit_targets:
            continue
        rows.append([site_key, f"https://{domain}/", "", "", "否"])

    rows.sort(key=lambda row: (row[4] != "是", row[1]))
    return rows


def build_legacy_history_rows(history_store: Optional[LegacyFeishuHistoryStore]) -> list[list[str]]:
    if not history_store:
        return []
    rows = []
    for record in history_store.records:
        rows.append(
            [
                record.source_title,
                record.source_url,
                record.source_root_domain,
                record.promoted_site_key,
                "是" if record.posted_flag else "否",
                record.posted_date_raw,
                record.legacy_tab_title,
                str(record.source_row),
                record.page_ascore,
            ]
        )
    rows.sort(key=lambda row: (row[3], row[2], row[1]))
    return rows


def build_legacy_source_library_rows(history_store: Optional[LegacyFeishuHistoryStore]) -> list[list[str]]:
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
            [
                source_row.source_title,
                source_row.source_url,
                source_row.source_root_domain,
                source_row.page_ascore,
                source_row.legacy_tab_title,
                source_row.tab_category,
                source_row.n_marker,
                source_row.b_marker,
                source_row.extra_value,
                str(source_row.source_row),
            ]
        )
    def sort_key(row: list[str]):
        try:
            score = float(row[3]) if str(row[3]).strip() else -1
        except Exception:
            score = -1
        has_marker = bool(str(row[6]).strip() or str(row[7]).strip())
        return (-score, 0 if has_marker else 1, row[2], row[1])

    rows.sort(key=sort_key)
    return rows
