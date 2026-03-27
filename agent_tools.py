"""
agent_tools.py
================
Agent 工具箱 - 将现有模块包装为 Gemini Function Calling 可识别的工具

设计原则：
  - 每个工具是一个普通 Python 函数（无副作用声明）
  - 工具函数的 docstring 就是给 AI 看的工具说明
  - 工具返回纯 JSON 可序列化的 dict，便于 Agent 读取
  - 所有工具函数都是"纯安全"的 - 失败时不会崩溃，只返回错误字段

使用方法（在 agent_core.py 中）：
  from agent_tools import TOOL_SCHEMAS, dispatch_tool_call
  schemas = TOOL_SCHEMAS          # 传给 Gemini API 的 tools 参数
  result = dispatch_tool_call("get_today_tasks", {})
"""

import json
import time
import traceback
from datetime import datetime
from typing import Any

# =====================================================================
# 工具函数定义
# =====================================================================

def get_today_tasks() -> dict:
    """
    调用飞书调度器，获取今日待发布的外链任务列表。
    返回任务列表（每条任务包含来源URL、目标站点、评论内容等）。
    在每个发帖周期开始时必须首先调用此工具。
    """
    try:
        import daily_scheduler
        result = daily_scheduler.main() or {}
        tasks = result.get("selected_tasks", [])
        today_success = result.get("today_success_by_site", {})
        return {
            "ok": True,
            "task_count": len(tasks),
            "tasks": tasks,
            "today_success_by_site": today_success,
            "message": f"成功获取 {len(tasks)} 条今日任务",
        }
    except Exception as exc:
        return {
            "ok": False,
            "task_count": 0,
            "tasks": [],
            "today_success_by_site": {},
            "message": f"获取任务失败: {str(exc)[:200]}",
        }


def post_backlink(source_url: str, target_site_key: str) -> dict:
    """
    对指定的来源页面执行自动发帖（外链发布）。
    会自动完成：导航到页面 → 生成评论内容 → 填写表单 → 提交 → 验证结果。
    参数：
      source_url: 要发布评论的来源页面 URL（博客/论坛文章页）
      target_site_key: 目标推广站点标识（如 bearclicker.net）
    """
    try:
        import daily_scheduler
        from form_automation_local import run_once

        # 从飞书中找到对应任务
        scheduler_result = daily_scheduler.main() or {}
        all_tasks = scheduler_result.get("selected_tasks", [])

        # 筛选匹配的任务
        matching_tasks = [
            t for t in all_tasks
            if (str(t.get("来源链接", "") or "").strip() == source_url.strip()
                or source_url.strip() in str(t.get("来源链接", "") or ""))
            and str(t.get("目标站标识", "") or "").strip() == target_site_key.strip()
        ]

        if not matching_tasks:
            # 如果没找到精确匹配任务，用全部任务里第一个该站点的任务
            matching_tasks = [
                t for t in all_tasks
                if str(t.get("目标站标识", "") or "").strip() == target_site_key.strip()
            ][:1]

        if not matching_tasks:
            return {
                "ok": False,
                "source_url": source_url,
                "target_site_key": target_site_key,
                "message": f"在今日任务中未找到匹配的任务（来源: {source_url}, 站点: {target_site_key}）",
            }

        start_time = time.time()
        batch_result = run_once(selected_tasks=matching_tasks, send_report=False) or {}
        elapsed = round(time.time() - start_time, 1)

        success_list = batch_result.get("success", [])
        failed_list = batch_result.get("failed", [])
        succeeded = len(success_list) > 0

        return {
            "ok": True,
            "source_url": source_url,
            "target_site_key": target_site_key,
            "succeeded": succeeded,
            "success_count": len(success_list),
            "failed_count": len(failed_list),
            "elapsed_seconds": elapsed,
            "message": f"发帖{'成功' if succeeded else '失败'}，耗时 {elapsed}s",
            "details": {
                "success": success_list[:3],
                "failed": failed_list[:3],
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "source_url": source_url,
            "target_site_key": target_site_key,
            "succeeded": False,
            "message": f"发帖过程中发生异常: {str(exc)[:200]}",
        }


def run_batch_posting(task_count: int = 5) -> dict:
    """
    批量执行今日所有待发布任务（一次运行多条）。
    自动从飞书获取任务、执行发帖、记录结果。
    参数：
      task_count: 本轮最多处理的任务数（默认5条）
    """
    try:
        import daily_scheduler
        from form_automation_local import run_once

        scheduler_result = daily_scheduler.main() or {}
        all_tasks = scheduler_result.get("selected_tasks", [])
        selected = all_tasks[:max(1, int(task_count))]

        if not selected:
            return {
                "ok": True,
                "message": "今日没有可处理的任务",
                "success_count": 0,
                "failed_count": 0,
                "today_success_by_site": scheduler_result.get("today_success_by_site", {}),
            }

        start_time = time.time()
        batch_result = run_once(selected_tasks=selected, send_report=False) or {}
        elapsed = round(time.time() - start_time, 1)

        success_list = batch_result.get("success", [])
        failed_list = batch_result.get("failed", [])

        return {
            "ok": True,
            "message": f"批量处理完成：成功 {len(success_list)} 条，失败 {len(failed_list)} 条，耗时 {elapsed}s",
            "success_count": len(success_list),
            "failed_count": len(failed_list),
            "elapsed_seconds": elapsed,
            "today_success_by_site": scheduler_result.get("today_success_by_site", {}),
            "details": {
                "success": success_list[:5],
                "failed": failed_list[:5],
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": f"批量发帖异常: {str(exc)[:200]}",
            "success_count": 0,
            "failed_count": 0,
        }


def get_daily_progress() -> dict:
    """
    查询今日所有站点的当前成功进度。
    返回每个站点已成功条数、目标条数以及是否已达标。
    用于 Agent 判断是否需要继续发帖，或已完成今日目标。
    """
    try:
        from sync_reporting_workbook import sync_reporting_workbook
        from backlink_state import STATUS_SUCCESS, iso_date

        today_str = datetime.now().strftime("%Y-%m-%d")
        sync_result = sync_reporting_workbook() or {}
        status_rows = sync_result.get("status_rows", [])
        targets = sync_result.get("targets", [])

        today_success_by_site: dict[str, int] = {}
        for row in status_rows:
            if row.get("状态") == STATUS_SUCCESS and iso_date(row.get("最近成功时间", "")) == today_str:
                site = str(row.get("目标站标识", "") or "")
                today_success_by_site[site] = today_success_by_site.get(site, 0) + 1

        active_targets = [t for t in targets if t.get("是否启用") == "是"]
        site_progress = []
        all_done = True

        for target in active_targets:
            site_key = str(target.get("站点标识", "") or "")
            daily_goal = int(str(target.get("每日成功目标", 10) or 10))
            today_success = today_success_by_site.get(site_key, 0)
            done = today_success >= daily_goal

            if not done:
                all_done = False

            site_progress.append({
                "site_key": site_key,
                "today_success": today_success,
                "daily_goal": daily_goal,
                "done": done,
                "remaining": max(0, daily_goal - today_success),
            })

        return {
            "ok": True,
            "today": today_str,
            "all_sites_done": all_done,
            "site_progress": site_progress,
            "message": "所有站点今日目标已完成！" if all_done else f"还有 {sum(s['remaining'] for s in site_progress)} 条任务待完成",
        }
    except Exception as exc:
        return {
            "ok": False,
            "all_sites_done": False,
            "message": f"获取进度失败: {str(exc)[:200]}",
            "site_progress": [],
        }


def send_daily_report(stop_reason: str = "Agent 自主完成") -> dict:
    """
    发送今日外链发布日报到飞书群组。
    在所有步骤执行完毕后调用，作为最后一步。
    参数：
      stop_reason: 本次运行结束的原因描述
    """
    try:
        from sync_reporting_workbook import sync_reporting_workbook
        from backlink_state import STATUS_SUCCESS, iso_date
        from webhook_sender import create_webhook_sender

        today_str = datetime.now().strftime("%Y-%m-%d")
        sync_result = sync_reporting_workbook() or {}
        status_rows = sync_result.get("status_rows", [])
        targets = [t for t in sync_result.get("targets", []) if t.get("是否启用") == "是"]

        today_success_by_site: dict[str, int] = {}
        for row in status_rows:
            if row.get("状态") == STATUS_SUCCESS and iso_date(row.get("最近成功时间", "")) == today_str:
                site = str(row.get("目标站标识", "") or "")
                today_success_by_site[site] = today_success_by_site.get(site, 0) + 1

        sender = create_webhook_sender()
        if not sender:
            return {"ok": False, "message": "未配置飞书 Webhook，跳过通知"}

        title = "🤖 [Agent] 外链自动化日报 | " + ", ".join(
            f"{k}:{v}" for k, v in sorted(today_success_by_site.items())
        )
        site_rows = []
        for target in targets:
            site_key = str(target.get("站点标识", "") or "")
            site_rows.append({
                "site_key": site_key,
                "today_success": today_success_by_site.get(site_key, 0),
                "daily_goal": int(str(target.get("每日成功目标", 10) or 10)),
                "run_success": today_success_by_site.get(site_key, 0),
                "run_failed": 0,
            })

        sent = sender.send_summary_report(title, {
            "sites": site_rows,
            "total_success": sum(today_success_by_site.values()),
            "total_failed": 0,
            "stop_reason": f"[Agent模式] {stop_reason}",
        })

        return {
            "ok": sent,
            "message": "飞书日报发送成功" if sent else "飞书日报发送失败",
            "today_success_by_site": today_success_by_site,
        }
    except Exception as exc:
        return {"ok": False, "message": f"发送日报异常: {str(exc)[:200]}"}


# =====================================================================
# Gemini Function Calling 工具 Schema（给 AI 看的工具描述）
# =====================================================================

# 注意：Gemini 1.47+ 使用 google.genai.types.Tool 格式
TOOL_FUNCTIONS = {
    "get_today_tasks": get_today_tasks,
    "post_backlink": post_backlink,
    "run_batch_posting": run_batch_posting,
    "get_daily_progress": get_daily_progress,
    "send_daily_report": send_daily_report,
}

# Gemini Function Declaration 格式的工具描述列表
TOOL_DECLARATIONS = [
    {
        "name": "get_today_tasks",
        "description": "调用飞书调度器，获取今日待发布的外链任务列表。在每个发帖周期开始时必须首先调用此工具。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "post_backlink",
        "description": "对指定的来源页面执行自动发帖（外链发布）。会自动完成导航、评论生成、表单填写、提交、结果验证。",
        "parameters": {
            "type": "object",
            "properties": {
                "source_url": {
                    "type": "string",
                    "description": "要发布评论的来源页面 URL（博客/论坛文章页）",
                },
                "target_site_key": {
                    "type": "string",
                    "description": "目标推广站点标识，如 bearclicker.net 或 nanobananaimage.org",
                },
            },
            "required": ["source_url", "target_site_key"],
        },
    },
    {
        "name": "run_batch_posting",
        "description": "批量执行今日所有待发布任务（推荐的主要执行方式）。自动从飞书获取任务并批量处理。",
        "parameters": {
            "type": "object",
            "properties": {
                "task_count": {
                    "type": "integer",
                    "description": "本轮最多处理的任务数，默认5条",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_daily_progress",
        "description": "查询今日所有站点的当前成功进度。用于 Agent 判断是否需要继续发帖，或已完成今日目标。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "send_daily_report",
        "description": "发送今日外链发布日报到飞书群组。在所有步骤执行完毕后调用，作为最后一步。",
        "parameters": {
            "type": "object",
            "properties": {
                "stop_reason": {
                    "type": "string",
                    "description": "本次运行结束的原因描述",
                },
            },
            "required": [],
        },
    },
]


def dispatch_tool_call(tool_name: str, tool_args: dict) -> dict:
    """
    根据 AI 的 Function Call 请求，分发并执行对应的工具函数。

    参数：
      tool_name - AI 请求调用的工具名
      tool_args - AI 传入的参数字典

    返回：
      工具函数的执行结果（dict）
    """
    func = TOOL_FUNCTIONS.get(tool_name)
    if not func:
        return {
            "ok": False,
            "message": f"未知工具 '{tool_name}'，可用工具：{list(TOOL_FUNCTIONS.keys())}",
        }
    try:
        return func(**tool_args) or {"ok": True, "message": "执行完成（无返回值）"}
    except Exception as exc:
        error_detail = traceback.format_exc()[-500:]
        return {
            "ok": False,
            "message": f"工具 '{tool_name}' 执行异常: {str(exc)[:200]}",
            "traceback": error_detail,
        }
