import json
from datetime import datetime
from pathlib import Path

from gws_integration import GoogleSheetsManager
from legacy_feishu_history import LegacyFeishuHistoryStore


def main():
    manager = GoogleSheetsManager()
    history_store = LegacyFeishuHistoryStore.from_config(force_refresh=True)
    if not history_store:
        print("❌ 旧飞书历史库不可用，无法分析。")
        return

    rows = manager.read_all_tasks()
    if len(rows) <= 1:
        print("ℹ️ 主表暂无数据。")
        return

    report = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "exact_duplicate_same_site": [],
        "same_domain_same_site": [],
        "legacy_marker_missing_mapping": [],
        "no_match": [],
    }

    for idx, row in enumerate(rows[1:], start=2):
        row_dict = {
            header: row[col_idx] if len(row) > col_idx else ""
            for col_idx, header in enumerate(manager.headers)
        }
        source_url = row_dict.get("URL", "")
        if not source_url:
            continue

        analysis = history_store.analyze(source_url, row_dict.get("Target_Website", ""))
        item = {
            "row_index": idx,
            "source_url": source_url,
            "target_website": row_dict.get("Target_Website", ""),
            "status": row_dict.get("Status", ""),
            "promoted_site_key": analysis.get("promoted_site_key"),
            "source_root_domain": analysis.get("source_root_domain", ""),
        }
        if analysis.get("exact_matches"):
            item["matches"] = analysis["exact_matches"][:3]
        if analysis.get("domain_matches"):
            item["matches"] = analysis["domain_matches"][:3]
        report[analysis["category"]].append(item)

    output_dir = Path("artifacts/legacy_history/reports")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"manual_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("✅ 旧飞书历史库去重分析完成")
    print(f"   硬重复: {len(report['exact_duplicate_same_site'])}")
    print(f"   同域提示: {len(report['same_domain_same_site'])}")
    print(f"   未映射: {len(report['legacy_marker_missing_mapping'])}")
    print(f"   未命中: {len(report['no_match'])}")
    print(f"   报告文件: {output_path}")


if __name__ == "__main__":
    main()
