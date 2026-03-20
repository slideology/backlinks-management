from __future__ import annotations

import json
from datetime import datetime

from backlink_state import STATUS_HEADERS, select_daily_tasks, sorted_target_rows
from feishu_workbook import FeishuWorkbook
from sync_reporting_workbook import sync_reporting_workbook


def load_config(config_path: str = "config.json") -> dict:
    defaults = {
        "scheduler": {
            "max_rounds_per_day": 5,
        },
    }
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception:
        return defaults

    merged = {**defaults, **config}
    merged["scheduler"] = {**defaults["scheduler"], **config.get("scheduler", {})}
    return merged


def main() -> dict:
    workbook = FeishuWorkbook.from_config()
    if not workbook:
        raise RuntimeError("飞书未正确配置，无法执行调度。")

    sync_result = sync_reporting_workbook(workbook=workbook)
    target_rows = sorted_target_rows(sync_result["targets"], active_only=True)
    status_rows = sync_result["status_rows"]
    selected_tasks, updated_rows, meta = select_daily_tasks(status_rows, target_rows, now=datetime.now())

    if selected_tasks:
        workbook.overwrite_sheet_dicts("records", STATUS_HEADERS, updated_rows)

    print("=" * 60)
    print(f"📅 飞书多站点调度 - {datetime.now().strftime('%Y-%m-%d')}")
    print("=" * 60)
    for target in target_rows:
        site_key = target.get("站点标识", "")
        today_success = meta["today_success_by_site"].get(site_key, 0)
        daily_goal = int(str(target.get("每日成功目标", 10) or 10))
        print(
            f"   站点 {site_key} | 今日已成功 {today_success}/{daily_goal}"
        )
    print(f"🧭 本轮新选任务: {len(selected_tasks)} 条")

    return {
        "selected_count": len(selected_tasks),
        "selected_tasks": selected_tasks,
        "today_success_by_site": meta["today_success_by_site"],
    }


if __name__ == "__main__":
    main()
