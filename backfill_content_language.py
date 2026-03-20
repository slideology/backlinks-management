import time
import re
from typing import Optional

from ai_generator import (
    generate_localized_bundle_for_target,
    load_active_target,
    translate_comment_to_chinese,
    translate_content_fields,
)
from feishu_integration import create_feishu_client
from gws_integration import GoogleSheetsManager
from page_context import fetch_page_context
from sheet_localization import (
    GOOGLE_HEADERS,
    contains_chinese,
    normalize_google_value,
    row_to_ordered_values,
    translate_row_for_storage,
)


CONTENT_COLUMNS = ("Keywords", "Anchor_Text", "Comment_Content")


def should_backfill_content(row_dict: dict) -> bool:
    return any(contains_chinese(str(row_dict.get(column, "") or "")) for column in CONTENT_COLUMNS)


def infer_anchor_seed(anchor_text: str, target_url: str) -> str:
    text = str(anchor_text or "")
    patterns = [
        r'<a[^>]*href=["\'][^"\']+["\'][^>]*>(.*?)</a>',
        r'\[url=[^\]]+\](.*?)\[/url\]',
        r'\[(.*?)\]\([^)]+\)',
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match and match.group(1).strip():
            return match.group(1).strip()
    if target_url:
        return re.sub(r"^www\.", "", re.sub(r"^https?://", "", target_url)).strip("/").split("/")[0]
    return "click here"


def build_row_target(row_dict: dict, active_target: Optional[dict]) -> dict:
    row_target_url = str(row_dict.get("Target_Website", "") or "").strip()
    if active_target and row_target_url and row_target_url == active_target.get("url", ""):
        return active_target
    return {
        "url": row_target_url,
        "anchor_text": infer_anchor_seed(row_dict.get("Anchor_Text", ""), row_target_url),
        "description": "",
    }


def main():
    print("=" * 60)
    print("🌍 回填关键词 / 锚文本 / 评论内容的目标语言")
    print("=" * 60)

    manager = GoogleSheetsManager()
    feishu_client = create_feishu_client()
    raw_rows = manager.read_all_tasks_raw()

    if len(raw_rows) <= 1:
        print("表格中没有可处理的数据。")
        return

    active_target = load_active_target()
    updated_rows = 0
    for row_index, row in enumerate(raw_rows[1:], start=2):
        row_dict = {}
        for idx, column in enumerate(GOOGLE_HEADERS):
            row_dict[column] = row[idx] if idx < len(row) else ""

        if not row_dict.get("URL") or (not should_backfill_content(row_dict) and row_dict.get("Comment_Content_ZH")):
            continue

        page_context = fetch_page_context(row_dict["URL"])
        language_name = page_context.get("language_name", "English")
        print(f"📝 第 {row_index} 行 -> {language_name} | {page_context.get('title', row_dict['URL'])[:60]}")

        row_target = build_row_target(row_dict, active_target)
        link_format = normalize_google_value("Link_Format", row_dict.get("Link_Format", "markdown"))
        if row_target.get("url"):
            content_bundle = generate_localized_bundle_for_target(row_target, link_format, page_context)
            translated_fields = {
                "Keywords": content_bundle["keywords"],
                "Anchor_Text": content_bundle["anchor_text"],
                "Comment_Content": content_bundle["comment_content"],
                "Comment_Content_ZH": content_bundle["comment_content_zh"],
            }
        else:
            translated_fields = translate_content_fields(
                {column: row_dict.get(column, "") for column in CONTENT_COLUMNS},
                language_name,
            )
            translated_fields["Comment_Content_ZH"] = translate_comment_to_chinese(
                translated_fields.get("Comment_Content", row_dict.get("Comment_Content", ""))
            )
        manager.update_task(row_index - 1, translated_fields)
        updated_rows += 1
        time.sleep(1.1)

        if feishu_client:
            row_dict.update(translated_fields)
            localized_row = translate_row_for_storage(row_dict)
            feishu_client.upsert_backlink_row(row_index, row_to_ordered_values(localized_row))

    print(f"✅ 已完成 {updated_rows} 行内容语言回填。")


if __name__ == "__main__":
    main()
