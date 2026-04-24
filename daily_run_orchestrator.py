import json
from collections import Counter
from datetime import datetime, time
from zoneinfo import ZoneInfo

import daily_scheduler
from backlink_state import STATUS_SUCCESS, iso_date
from form_automation_local import run_once
from sync_reporting_workbook import sync_reporting_workbook
from webhook_sender import create_webhook_sender


def load_config(config_path: str = "config.json"):
    defaults = {
        "scheduler": {
            "max_rounds_per_day": 20,
            "max_idle_rounds_per_day": 2,
            "timezone": "Asia/Shanghai",
            "run_windows": [
                {"start": "08:00", "end": "10:00"},
                {"start": "12:00", "end": "14:00"},
            ],
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


def _count_today_success_by_site(status_rows: list[dict], today_str: str) -> dict[str, int]:
    counts = {}
    for row in status_rows:
        if row.get("状态") == STATUS_SUCCESS and iso_date(row.get("最近成功时间", "")) == today_str:
            site_key = row.get("目标站标识", "")
            counts[site_key] = counts.get(site_key, 0) + 1
    return counts


def _parse_window_clock(value: str) -> time:
    hour_text, minute_text = str(value or "").split(":", 1)
    return time(hour=int(hour_text), minute=int(minute_text))


def _resolve_run_window(now: datetime, windows: list[dict]) -> dict | None:
    for window in windows or []:
        try:
            start_clock = _parse_window_clock(window.get("start", ""))
            end_clock = _parse_window_clock(window.get("end", ""))
        except Exception:
            continue
        if start_clock <= now.time() < end_clock:
            return {
                "start": start_clock,
                "end": end_clock,
                "label": f"{start_clock.strftime('%H:%M')}-{end_clock.strftime('%H:%M')}",
            }
    return None


def main():
    config = load_config()

    # =====================================================================
    # 🤖 Agent 模式开关（在 config.json 中 "agent": {"enabled": true} 启用）
    # =====================================================================
    agent_config = config.get("agent", {})
    agent_enabled = bool(agent_config.get("enabled", False))

    if agent_enabled:
        print("\n" + "=" * 60)
        print("🤖 Agent 模式已启用 - 移交给 BacklinkAgent 自主运行")
        print("=" * 60)
        try:
            from agent_core import BacklinkAgent
            agent = BacklinkAgent()
            result = agent.run_daily_session()
            print(f"\n✅ Agent 日程完成：{result.get('stop_reason', '未知')}")
            return
        except Exception as exc:
            print(f"\n⚠️ Agent 模式启动失败（{exc}），自动降级到传统模式继续运行...")
            # ↓ 降级：继续执行下方的原有流水线逻辑

    # =====================================================================
    # 📋 传统模式（原有定序流水线，Agent 降级时也走这里）
    # =====================================================================
    scheduler_cfg = config["scheduler"]
    timezone_name = str(scheduler_cfg.get("timezone", "Asia/Shanghai") or "Asia/Shanghai")
    now_local = datetime.now(ZoneInfo(timezone_name))
    active_window = _resolve_run_window(now_local, scheduler_cfg.get("run_windows", []))
    if not active_window:
        print("=" * 60)
        print(f"🕒 当前时间 {now_local.strftime('%Y-%m-%d %H:%M:%S')} 不在允许运行窗口内，跳过本轮。")
        print("=" * 60)
        return

    configured_max_rounds = int(scheduler_cfg.get("max_rounds_per_day", 20) or 0)
    max_rounds = configured_max_rounds if configured_max_rounds > 0 else 1000000
    today_str = now_local.strftime("%Y-%m-%d")

    print("=" * 60)
    print(f"🚦 飞书多站点日总控启动 - {today_str}")
    print(f"🕒 当前运行窗口: {active_window['label']} ({timezone_name})")
    print(f"🔁 最多轮次: {'无限制' if configured_max_rounds <= 0 else max_rounds}")
    print("=" * 60)

    all_success = []
    all_failed = []
    final_counts = {}
    stop_reason = "候选任务耗尽"
    idle_rounds = 0
    finished_all_rounds = True

    for round_idx in range(1, max_rounds + 1):
        round_now = datetime.now(ZoneInfo(timezone_name))
        if round_now.time() >= active_window["end"]:
            stop_reason = f"达到运行窗口结束时间（{active_window['label']}）"
            finished_all_rounds = False
            break

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
        round_success_count = len(batch_result.get("success", []))

        joined_counts = ", ".join(
            f"{target.get('站点标识', '')}:{final_counts.get(target.get('站点标识', ''), 0)}/{target.get('每日成功目标', 10)}"
            for target in active_targets
        )
        print(f"📊 第 {round_idx} 轮结束：{joined_counts or '无启用站点'}")

        if selected_tasks:
            if round_success_count == 0:
                idle_rounds += 1
            else:
                idle_rounds = 0

            configured_idle_rounds = int(scheduler_cfg.get("max_idle_rounds_per_day", 2) or 0)
            if configured_idle_rounds > 0 and idle_rounds >= configured_idle_rounds:
                stop_reason = f"连续 {idle_rounds} 轮无新增成功，提前停止"
                finished_all_rounds = False
                break

        if active_targets and all(
            final_counts.get(target.get("站点标识", ""), 0) >= int(str(target.get("每日成功目标", 10) or 10))
            for target in active_targets
        ):
            stop_reason = "所有启用站点均已达到当日成功目标"
            finished_all_rounds = False
            break

        if not selected_tasks:
            stop_reason = "今日无可继续处理的新任务"
            finished_all_rounds = False
            break

    if finished_all_rounds:
        stop_reason = (
            f"达到最大轮次上限（{max_rounds} 轮）"
            if configured_max_rounds > 0
            else "达到内部保护上限"
        )

    print(f"\n🏁 日总控结束：{stop_reason}")

    sender = create_webhook_sender()
    should_notify = bool(active_targets) and all(
        final_counts.get(target.get("站点标识", ""), 0) >= int(str(target.get("每日成功目标", 10) or 10))
        for target in active_targets
    )
    if sender and should_notify:
        title = "🌍 外链自动化日报 | " + ", ".join(f"{site}:{count}" for site, count in sorted(final_counts.items()))
        success_by_site = Counter(str(item.get("site_key", "") or "") for item in all_success)
        failed_by_site = Counter(str(item.get("site_key", "") or "") for item in all_failed)
        site_rows = []
        for target in active_targets:
            site_key = str(target.get("站点标识", "") or "")
            site_rows.append(
                {
                    "site_key": site_key,
                    "today_success": final_counts.get(site_key, 0),
                    "daily_goal": int(str(target.get("每日成功目标", 10) or 10)),
                    "run_success": success_by_site.get(site_key, 0),
                    "run_failed": failed_by_site.get(site_key, 0),
                }
            )
        sender.send_summary_report(
            title,
            {
                "sites": site_rows,
                "total_success": len(all_success),
                "total_failed": len(all_failed),
                "stop_reason": stop_reason,
            },
        )
    elif sender:
        print("ℹ️ 当日目标未全部达到，跳过飞书最终通知。")
    else:
        print("ℹ️ 未配置飞书 Webhook，跳过通知。")


if __name__ == "__main__":
    main()
