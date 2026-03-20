import json
import re
import ast
from pathlib import Path
from typing import Optional


GOOGLE_HEADERS = [
    "ID",
    "Type",
    "URL",
    "Discovered_From",
    "Has_Captcha",
    "Link_Strategy",
    "Link_Format",
    "Has_URL_Field",
    "Status",
    "Priority",
    "Target_Website",
    "Keywords",
    "Anchor_Text",
    "Comment_Content",
    "Comment_Content_ZH",
    "Execution_Date",
    "Success_URL",
    "Notes",
    "Last_Updated",
    "Daily_Batch",
]

HEADER_LABELS_ZH = {
    "ID": "编号",
    "Type": "类型",
    "URL": "外链链接",
    "Discovered_From": "来源",
    "Has_Captcha": "有验证码",
    "Link_Strategy": "链接策略",
    "Link_Format": "链接格式",
    "Has_URL_Field": "有网址字段",
    "Status": "状态",
    "Priority": "优先级",
    "Target_Website": "目标网站",
    "Keywords": "关键词",
    "Anchor_Text": "锚文本",
    "Comment_Content": "评论内容",
    "Comment_Content_ZH": "评论内容中文",
    "Execution_Date": "执行日期",
    "Success_URL": "成功发布链接",
    "Notes": "备注",
    "Last_Updated": "最后更新",
    "Daily_Batch": "每日批次",
}

FEISHU_HEADERS_ZH = [HEADER_LABELS_ZH[header] for header in GOOGLE_HEADERS]

TRANSLATION_CACHE_PATH = Path(".translation_cache.json")
LOCALIZED_TEXT_COLUMNS = {"Notes", "Comment_Content_ZH"}
MULTILINGUAL_CONTENT_COLUMNS = {"Keywords", "Anchor_Text", "Comment_Content"}
URL_COLUMNS = {"URL", "Target_Website", "Success_URL"}
PASSTHROUGH_COLUMNS = {"ID", "Execution_Date", "Last_Updated"}

ENUM_TRANSLATIONS = {
    "Type": {
        "profile": "资料页",
        "forum": "论坛页",
        "blog": "博客页",
        "article": "文章页",
        "directory": "目录页",
        "community": "社区页",
        "comment": "评论页",
    },
    "Has_Captcha": {
        "yes": "是",
        "no": "否",
    },
    "Has_URL_Field": {
        "yes": "是",
        "no": "否",
    },
    "Link_Strategy": {
        "url_field": "仅网址字段",
        "in_content": "正文内嵌",
        "both": "两者都支持",
    },
    "Link_Format": {
        "html": "HTML",
        "bbcode": "BBCode",
        "markdown": "Markdown",
        "url_field": "网址字段",
        "unknown": "未知",
        "plain_text": "纯文本",
        "plain_text_autolink": "自动链接",
    },
    "Status": {
        "pending": "待处理",
        "in_progress": "进行中",
        "completed": "已完成",
        "failed": "失败",
    },
    "Priority": {
        "high": "高",
        "medium": "中",
        "low": "低",
    },
}

REVERSE_ENUM_TRANSLATIONS = {}
for column, mapping in ENUM_TRANSLATIONS.items():
    reverse_map = {}
    for internal_value, display_value in mapping.items():
        reverse_map[internal_value.lower()] = internal_value
        reverse_map[display_value.lower()] = internal_value
    REVERSE_ENUM_TRANSLATIONS[column] = reverse_map

NOTE_GLOSSARY = {
    "Vision": "视觉识别",
    "Textarea": "文本框",
    "Timeout": "超时",
    "exceeded": "已超出限制",
    "strict mode violation": "严格模式冲突",
    "Target closed": "目标窗口已关闭",
    "Protocol error": "协议错误",
    "waiting for selector": "等待选择器",
}


def contains_chinese(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value or ""))


def is_url_like(value: str) -> bool:
    text = str(value or "").strip()
    return bool(re.match(r"^(https?://|www\.)", text, re.IGNORECASE))


def looks_translatable(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and bool(re.search(r"[A-Za-z]", text)) and not contains_chinese(text)


def needs_free_text_translation(column: str, value: str) -> bool:
    text = str(value or "").strip()
    if not text or not re.search(r"[A-Za-z]", text):
        return False
    if column == "Notes":
        return looks_translatable(text)
    return False


def normalize_batch_token(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("批次-"):
        return f"Batch-{text[3:]}"
    return text


def display_batch_token(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("Batch-"):
        return f"批次-{text[6:]}"
    return text


def normalize_google_value(column: str, value) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    if column in REVERSE_ENUM_TRANSLATIONS:
        return REVERSE_ENUM_TRANSLATIONS[column].get(text.lower(), text)
    if column == "Daily_Batch":
        return normalize_batch_token(text)
    return text


def normalize_google_row(row: list[str]) -> list[str]:
    normalized = list(row)
    for index, header in enumerate(GOOGLE_HEADERS):
        if index < len(normalized):
            normalized[index] = normalize_google_value(header, normalized[index])
    return normalized


def localize_note_phrases(text: str) -> str:
    localized = str(text or "")
    if localized.startswith("{") and "text" in localized:
        try:
            payload = ast.literal_eval(localized)
            if isinstance(payload, dict) and payload.get("text"):
                localized = str(payload["text"])
        except Exception:
            pass
    for english, chinese in NOTE_GLOSSARY.items():
        localized = localized.replace(english, chinese)
    return localized


def _load_cache() -> dict:
    if not TRANSLATION_CACHE_PATH.exists():
        return {}
    try:
        return json.loads(TRANSLATION_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    TRANSLATION_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _parse_json_payload(text: str) -> Optional[dict]:
    if not text:
        return None
    stripped = text.strip()
    candidates = [stripped]
    code_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
    if code_match:
        candidates.insert(0, code_match.group(1))
    brace_match = re.search(r"(\{.*\})", stripped, re.DOTALL)
    if brace_match:
        candidates.append(brace_match.group(1))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return None


def translate_fields_to_chinese(fields: dict[str, str]) -> dict[str, str]:
    indexed_fields = {key: (key, value) for key, value in fields.items()}
    return translate_indexed_fields_to_chinese(indexed_fields)


def translate_indexed_fields_to_chinese(indexed_fields: dict[str, tuple[str, str]]) -> dict[str, str]:
    if not indexed_fields:
        return {}

    cache = _load_cache()
    translated = {}
    pending = {}

    for synthetic_key, payload in indexed_fields.items():
        column, value = payload
        text = str(value or "")
        cache_key = f"{column}::{text}"
        if cache_key in cache:
            translated[synthetic_key] = cache[cache_key]
            continue
        pending[synthetic_key] = {"column": column, "text": text}

    if pending:
        from ai_generator import MODEL_ID, client

        prompt = f"""
你是一个表格翻译助手。请把下面 JSON 里的字段值翻译成简体中文，并严格返回 JSON。

要求：
1. 保留所有 URL、域名、邮箱地址、日期、时间、HTML 标签、Markdown 链接、BBCode、换行和代码格式不变。
2. 只翻译自然语言部分，不能删除内容，不能新增解释。
3. 返回结果必须是一个 JSON 对象，键名必须与输入完全一致，值必须是翻译后的字符串。
4. 如果原文已经是中文或不需要翻译，就原样返回。

输入 JSON：
{json.dumps(pending, ensure_ascii=False, indent=2)}

返回示例：
{{
  "row_2_Keywords": "中文结果",
  "row_2_Notes": "中文结果"
}}
"""
        response = client.models.generate_content(model=MODEL_ID, contents=prompt)
        payload = _parse_json_payload(getattr(response, "text", ""))
        if not payload:
            raise RuntimeError("Gemini 未返回可解析的 JSON 翻译结果。")
        for synthetic_key, value in pending.items():
            column = value["column"]
            original_text = value["text"]
            translated_payload = payload.get(synthetic_key, original_text)
            if isinstance(translated_payload, dict):
                translated_payload = translated_payload.get("text", original_text)
            translated_value = str(translated_payload)
            translated[synthetic_key] = translated_value
            cache[f"{column}::{original_text}"] = translated_value
        _save_cache(cache)

    return translated


def localize_basic_value(column: str, value) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        return ""
    if column in URL_COLUMNS or column in PASSTHROUGH_COLUMNS:
        return text
    if column in ENUM_TRANSLATIONS:
        internal = normalize_google_value(column, text)
        return ENUM_TRANSLATIONS[column].get(internal, text)
    if column == "Discovered_From":
        source_match = re.match(r"^([A-Za-z0-9_-]+):(.*)$", text)
        if source_match:
            source_name = source_match.group(1).upper() if source_match.group(1).lower() == "ahrefs" else source_match.group(1)
            return f"{source_name}：{source_match.group(2)}"
        return text
    if column == "Daily_Batch":
        return display_batch_token(text)
    if column == "Notes":
        return localize_note_phrases(text)
    return text


def translate_row_for_storage(row_dict: dict[str, str]) -> dict[str, str]:
    localized = {}
    pending_ai_fields = {}

    for column in GOOGLE_HEADERS:
        value = str(row_dict.get(column, "") or "")
        if column in LOCALIZED_TEXT_COLUMNS and needs_free_text_translation(column, value):
            pending_ai_fields[column] = value
            continue
        localized[column] = localize_basic_value(column, value)

    if pending_ai_fields:
        localized.update(translate_fields_to_chinese(pending_ai_fields))

    if "Notes" in localized:
        localized["Notes"] = localize_note_phrases(localized["Notes"])

    for column in LOCALIZED_TEXT_COLUMNS:
        localized.setdefault(column, str(row_dict.get(column, "") or ""))

    return localized


def localize_updates_for_storage(updates: dict[str, str]) -> dict[str, str]:
    localized = {}
    for column, value in updates.items():
        text = str(value or "")
        if column in LOCALIZED_TEXT_COLUMNS and needs_free_text_translation(column, text):
            localized[column] = translate_fields_to_chinese({column: text})[column]
            continue
        localized[column] = localize_basic_value(column, text)
    if "Notes" in localized:
        localized["Notes"] = localize_note_phrases(localized["Notes"])
    return localized


def row_to_ordered_values(row_dict: dict[str, str], headers: Optional[list[str]] = None) -> list[str]:
    order = headers or GOOGLE_HEADERS
    return [str(row_dict.get(header, "") or "") for header in order]
