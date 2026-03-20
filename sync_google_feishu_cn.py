from feishu_integration import create_feishu_client
from gws_integration import GoogleSheetsManager
from sheet_localization import (
    GOOGLE_HEADERS,
    LOCALIZED_TEXT_COLUMNS,
    localize_basic_value,
    localize_note_phrases,
    needs_free_text_translation,
    row_to_ordered_values,
    translate_indexed_fields_to_chinese,
)
from ai_generator import translate_comment_to_chinese
import time


CHUNK_SIZE = 12


def localize_chunk(chunk_rows: list[tuple[int, dict[str, str]]]) -> list[tuple[int, dict[str, str]]]:
    pending_translations = {}
    localized_rows = []
    row_lookup = {row_index: row_dict for row_index, row_dict in chunk_rows}

    for row_index, row_dict in chunk_rows:
        localized = {}
        for column in GOOGLE_HEADERS:
            value = str(row_dict.get(column, "") or "")
            if column in LOCALIZED_TEXT_COLUMNS and needs_free_text_translation(column, value):
                pending_translations[f"row_{row_index}_{column}"] = (column, value)
                continue
            localized[column] = localize_basic_value(column, value)
        localized_rows.append((row_index, localized))

    translated = translate_indexed_fields_to_chinese(pending_translations)
    for row_index, localized in localized_rows:
        for column in LOCALIZED_TEXT_COLUMNS:
            token = f"row_{row_index}_{column}"
            if token in translated:
                localized_value = translated[token]
                if column == "Notes":
                    localized_value = localize_note_phrases(localized_value)
                localized[column] = localized_value
            else:
                localized.setdefault(column, str(row_lookup[row_index].get(column, "") or ""))

    return localized_rows


def main():
    print("=" * 60)
    print("🔄 Google Sheets 与飞书表格中文同步")
    print("=" * 60)

    manager = GoogleSheetsManager()
    feishu_client = create_feishu_client()
    if not feishu_client:
        raise RuntimeError("飞书未正确配置，无法执行同步。")

    raw_rows = manager.read_all_tasks_raw()
    if len(raw_rows) <= 1:
        print("表格没有可同步的数据。")
        return

    header = raw_rows[0]
    if header[: len(GOOGLE_HEADERS)] != GOOGLE_HEADERS:
        raise RuntimeError("Google Sheets 表头与当前代码预期不一致，请先确认主表结构。")

    translated_count = 0
    feishu_rows = []
    pending_chunk = []

    for row_index, row in enumerate(raw_rows[1:], start=2):
        row_dict = {}
        for idx, column in enumerate(GOOGLE_HEADERS):
            row_dict[column] = row[idx] if idx < len(row) else ""
        pending_chunk.append((row_index, row_dict))

        is_last_row = row_index == len(raw_rows)
        if len(pending_chunk) < CHUNK_SIZE and not is_last_row:
            continue

        localized_chunk = localize_chunk(pending_chunk)
        for (localized_row_index, original_row), (_, localized_row) in zip(pending_chunk, localized_chunk):
            original_row = dict(original_row)
            updates = {}
            for column in GOOGLE_HEADERS:
                original = str(original_row.get(column, "") or "")
                localized = str(localized_row.get(column, "") or "")
                if original != localized and column in manager.col_map:
                    updates[column] = localized

            comment_translation = str(original_row.get("Comment_Content_ZH", "") or "").strip()
            comment_content = str(localized_row.get("Comment_Content", "") or original_row.get("Comment_Content", "") or "").strip()
            if not comment_translation and comment_content:
                translated_comment = translate_comment_to_chinese(comment_content)
                updates["Comment_Content_ZH"] = translated_comment
                localized_row["Comment_Content_ZH"] = translated_comment

            if updates:
                translated_count += 1
                print(f"📝 正在中文化 Google 第 {localized_row_index} 行...")
                manager.update_task(localized_row_index - 1, updates)
                time.sleep(1.1)

            feishu_rows.append(row_to_ordered_values(localized_row))

        pending_chunk = []

    total_rows = feishu_client.overwrite_backlink_rows(feishu_rows)
    print(f"✅ Google 已更新 {translated_count} 行。")
    print(f"✅ 飞书已覆盖同步 {len(feishu_rows)} 条数据（写入到第 {total_rows} 行）。")


if __name__ == "__main__":
    main()
