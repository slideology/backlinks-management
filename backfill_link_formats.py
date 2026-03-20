import time

from feishu_integration import create_feishu_client
from gws_integration import GoogleSheetsManager
from sheet_localization import GOOGLE_HEADERS, normalize_google_value, row_to_ordered_values, translate_row_for_storage
from website_format_detector import WebsiteFormatDetector


def main():
    print("=" * 60)
    print("🔎 回填 Link_Format（unknown/空值）")
    print("=" * 60)

    manager = GoogleSheetsManager()
    feishu_client = create_feishu_client()
    detector = WebsiteFormatDetector()
    raw_rows = manager.read_all_tasks_raw()

    if len(raw_rows) <= 1:
        print("表格中没有可处理的数据。")
        return

    updated_rows = 0
    for row_index, row in enumerate(raw_rows[1:], start=2):
        row_dict = {}
        for idx, column in enumerate(GOOGLE_HEADERS):
            row_dict[column] = row[idx] if idx < len(row) else ""

        url = str(row_dict.get("URL", "") or "").strip()
        current_format = normalize_google_value("Link_Format", row_dict.get("Link_Format", ""))
        if not url or current_format not in {"", "unknown"}:
            continue

        analysis = detector.analyze_website(url)
        recommended = normalize_google_value("Link_Format", analysis.get("recommended_format", "unknown"))
        if not recommended or recommended == "unknown":
            continue

        print(
            f"📝 第 {row_index} 行 -> {recommended} | "
            f"证据={analysis.get('evidence_type', 'unknown')} | {analysis.get('title', url)[:60]}"
        )
        manager.update_task(row_index - 1, {"Link_Format": recommended})
        updated_rows += 1
        time.sleep(1.1)

        if feishu_client:
            try:
                row_dict["Link_Format"] = recommended
                localized_row = translate_row_for_storage(row_dict)
                feishu_client.upsert_backlink_row(row_index, row_to_ordered_values(localized_row))
            except Exception as exc:
                print(f"  ⚠️ 飞书同步失败（稍后可跑整表同步补齐）: {exc}")

    print(f"✅ 已完成 {updated_rows} 行 Link_Format 回填。")


if __name__ == "__main__":
    main()
