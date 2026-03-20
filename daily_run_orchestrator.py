import json
from datetime import datetime

import daily_scheduler
from backlink_state import STATUS_SUCCESS, iso_date
from form_automation_local import run_once
from sync_reporting_workbook import sync_reporting_workbook
from webhook_sender import create_webhook_sender


def load_config(config_path: str = "config.json"):
    defaults = {
        "scheduler": {"max_rounds_per_day": 5},
    }
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except Exception:
        return defaults

    merged = {**defaults, **config}
    merged["scheduler"] = {**defaults["scheduler"], **config.get("scheduler", {})}
    return merged


def _count_today_success_by_site(status_rows: list[dict], today_str: str) -> dict[str, int]:
    counts = {}
    for row in status_rows:
        if row.get("状态") == STATUS_SUCCESS and iso_date(row.get("最近成功时间", "")) == today_str:
            site_key = row.get("目标站标识", "")
            counts[site_key] = counts.get(site_key, 0) + 1
    return counts


def main():
    config = load_config()
    max_rounds = max(1, int(config["scheduler"].get("max_rounds_per_day", 5)))
    today_str = datetime.now().strftime("%Y-%m-%d")

    print("=" * 60)
    print(f"🚦 飞书多站点日总控启动 - {today_str}")
    print(f"🔁 最多轮次: {max_rounds}")
    print("=" * 60)

    all_success = []
    all_failed = []
    final_counts = {}
    stop_reason = "候选任务耗尽"

    for round_idx in range(1, max_rounds + 1):
        print(f"\n{'=' * 60}")
        print(f"🔄 第 {round_idx}/{max_rounds} 轮开始")
        print(f"{'=' * 60}")

        schedule_result = daily_scheduler.main() or {}
        selected_tasks = schedule_result.get("selected_tasks", [])
        batch_result = run_once(selected_tasks=selected_tasks, send_report=False) or {}

        all_success.extend(batch_result.get("success", []))
        all_failed.extend(batch_result.get("failed", []))

        sync_result = sync_reporting_workbook()
        final_counts = _count_today_success_by_site(sync_result.get("status_rows", []), today_str)
        active_targets = [row for row in sync_result.get("targets", []) if row.get("是否启用") == "是"]

        joined_counts = ", ".join(
            f"{target.get('站点标识', '')}:{final_counts.get(target.get('站点标识', ''), 0)}/{target.get('每日成功目标', 10)}"
            for target in active_targets
        )
        print(f"📊 第 {round_idx} 轮结束：{joined_counts or '无启用站点'}")

        if active_targets and all(
            final_counts.get(target.get("站点标识", ""), 0) >= int(str(target.get("每日成功目标", 10) or 10))
            for target in active_targets
        ):
            stop_reason = "所有启用站点均已达到当日成功目标"
            break

        if not selected_tasks:
            stop_reason = "今日无可继续处理的新任务"
            break

    print(f"\n🏁 日总控结束：{stop_reason}")

    sender = create_webhook_sender()
    if sender:
        title = "🌍 外链自动化日报 | " + ", ".join(f"{site}:{count}" for site, count in sorted(final_counts.items()))
        sender.send_detailed_report(title, {"success": all_success, "failed": all_failed})
    else:
        print("ℹ️ 未配置飞书 Webhook，跳过通知。")


if __name__ == "__main__":
    main()
