from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import datetime
from pathlib import Path

from backlink_state import (
    LEGACY_HISTORY_HEADERS,
    LEGACY_SOURCE_LIBRARY_HEADERS,
    STATUS_HEADERS,
    TARGET_SITE_HEADERS,
    dynamic_source_headers,
)
from feishu_workbook import FeishuWorkbook, load_reporting_config, save_state
from recreate_feishu_workbook_from_xlsx import create_feishu_user_client, update_feishu_config
from sync_reporting_workbook import build_reporting_snapshot


def backup_state_file(state_path: Path) -> None:
    if not state_path.exists():
        return
    backup = state_path.with_name(f"{state_path.stem}.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    shutil.copy2(state_path, backup)
    print(f"🗂️ 已备份旧 state: {backup}")


def create_new_reporting_workbook(config_path: Path, title: str) -> tuple[FeishuWorkbook, dict]:
    reporting_config = load_reporting_config(str(config_path))
    client = create_feishu_user_client(str(config_path))
    created = client.create_spreadsheet(title, as_user=True)
    spreadsheet_token = created["spreadsheet_token"]
    spreadsheet_url = created.get("url", "")

    sheet_ids: dict[str, str] = {}
    first_sheet_id = client.get_sheet_id_by_token(spreadsheet_token, as_user=True)
    source_title = reporting_config["sheet_titles"]["sources"]
    client.rename_sheet(first_sheet_id, source_title, spreadsheet_token=spreadsheet_token, as_user=True)
    sheet_ids["sources"] = first_sheet_id

    for key, sheet_title in reporting_config["sheet_titles"].items():
        if key == "sources":
            continue
        sheet_ids[key] = client.ensure_sheet(sheet_title, spreadsheet_token=spreadsheet_token, as_user=True)

    state_payload = {
        "spreadsheet_token": spreadsheet_token,
        "spreadsheet_url": spreadsheet_url,
        "sheet_ids": sheet_ids,
    }
    state_path = Path(reporting_config["state_file"])
    backup_state_file(state_path)
    save_state(state_path, state_payload)
    update_feishu_config(config_path, spreadsheet_token, sheet_ids)

    workbook = FeishuWorkbook(
        client=client,
        config=reporting_config,
        spreadsheet_token=spreadsheet_token,
        sheet_ids=sheet_ids,
        spreadsheet_url=spreadsheet_url,
    )
    return workbook, state_payload


def main() -> None:
    parser = argparse.ArgumentParser(description="从当前飞书数据快照重建一个全新的飞书工作簿")
    parser.add_argument("--config", default="config.json", help="项目配置文件路径")
    parser.add_argument("--title", default="", help="新建飞书工作簿标题")
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    reporting_config = load_reporting_config(str(config_path))
    workbook_title = args.title.strip() or reporting_config.get("workbook_title", "外链运营总表")

    old_workbook = FeishuWorkbook.from_config(str(config_path))
    if not old_workbook:
        raise RuntimeError("当前飞书工作簿未正确配置，无法读取现有数据。")

    print("📚 正在读取当前工作簿快照...")
    snapshot = build_reporting_snapshot(old_workbook)
    targets = snapshot["targets"]
    source_headers = dynamic_source_headers(targets)

    print(f"🆕 正在创建新的飞书工作簿: {workbook_title}")
    new_workbook, _ = create_new_reporting_workbook(config_path, workbook_title)

    print("📤 正在回填 5 个 tab ...")
    new_workbook.overwrite_sheet_dicts("sources", source_headers, snapshot["source_rows"])
    time.sleep(1)
    new_workbook.overwrite_sheet_dicts("records", STATUS_HEADERS, snapshot["status_rows"])
    time.sleep(1)
    new_workbook.overwrite_sheet_dicts("targets", TARGET_SITE_HEADERS, targets)
    time.sleep(1)
    new_workbook.overwrite_sheet_dicts("history", LEGACY_HISTORY_HEADERS, snapshot["history_rows"])
    time.sleep(1)
    new_workbook.overwrite_sheet_dicts("library", LEGACY_SOURCE_LIBRARY_HEADERS, snapshot["library_rows"])

    print("✅ 新工作簿已切换完成")
    print(new_workbook.spreadsheet_url or new_workbook.spreadsheet_token)


if __name__ == "__main__":
    main()
