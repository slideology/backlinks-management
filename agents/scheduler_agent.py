"""
agents/scheduler_agent.py
==========================
Scheduler Agent - 负责智能任务选择和站点优先级排序

职责（就像一个聪明的项目经理）：
  - 从飞书获取今日待发布的站点列表
  - 结合 AgentMemory 历史数据，判断每个站点的优先级
  - 把"高成功率的站点"排在前面，把"黑名单站点"直接过滤
  - 将有序的任务列表交给 Supervisor，再转发给 Executor

接收消息 (type="task")：
  { "action": "select_tasks", "task_count": 5 }

返回消息 (type="result")：
  { "success": True, "tasks": [...], "skipped_blacklisted": 2, "priority_info": "..." }
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base_agent import AgentMessage, BaseAgent, resolve_multi_agent_model


class SchedulerAgent(BaseAgent):
    """
    智能任务调度 Agent。

    核心功能：
      1. 拉取飞书任务
      2. 过滤黑名单站点（省时间）
      3. 按成功率排序（高成功率站点优先，提升整体完成速度）
      4. 返回给 Supervisor 有序的任务列表
    """

    def __init__(self, config_path: str = "config.json"):
        super().__init__(
            name="SchedulerAgent",
            role_description="智能任务调度员，负责从飞书获取任务并按成功率排序，过滤黑名单站点",
            model=resolve_multi_agent_model(config_path, "scheduler", "gemini-flash-lite-latest"),
            config_path=config_path,
        )
        # 懒加载记忆模块
        self._memory = None

    def _get_memory(self):
        if self._memory is None:
            from agent_memory import AgentMemory
            self._memory = AgentMemory()
        return self._memory

    def handle_message(self, message: AgentMessage) -> AgentMessage:
        """
        处理来自 Supervisor 的调度请求。
        """
        action = message.payload.get("action", "")
        self.log(f"收到任务: {action}")

        if action == "select_tasks":
            return self._select_and_prioritize(message)
        elif action == "get_progress":
            return self._get_progress(message)
        else:
            return AgentMessage.error(
                self.name, message.from_agent,
                f"未知指令 '{action}'，支持: select_tasks / get_progress"
            )

    def _select_and_prioritize(self, message: AgentMessage) -> AgentMessage:
        """
        拉取飞书任务 → 过滤黑名单 → 按成功率排序。
        """
        task_count = int(message.payload.get("task_count", 5) or 5)
        try:
            import daily_scheduler
            result = daily_scheduler.main() or {}
            all_tasks = result.get("selected_tasks", [])
            today_success = result.get("today_success_by_site", {})

            if not all_tasks:
                self.log("今日无可用任务", "WARN")
                return AgentMessage.result(
                    self.name, message.from_agent,
                    success=True,
                    tasks=[],
                    skipped_blacklisted=0,
                    today_success_by_site=today_success,
                    priority_info="今日无任务",
                )

            memory = self._get_memory()

            # 步骤 1：过滤黑名单
            clean_tasks = []
            blacklisted_count = 0
            for task in all_tasks:
                url = str(task.get("来源链接", "") or "")
                if memory.is_blacklisted(url):
                    blacklisted_count += 1
                    self.log(f"跳过黑名单站点: {url[:50]}", "WARN")
                else:
                    clean_tasks.append(task)

            # 步骤 2：按历史成功率排序（高成功率的先做）
            def _sort_key(task):
                url = str(task.get("来源链接", "") or "")
                profile = memory.get_site_profile(url)
                # 成功率越高、历史次数越多 → 优先级越高
                rate = profile.get("success_rate", 0.5)
                attempts = profile.get("attempts", 0)
                is_new = attempts == 0
                # 新站点放中间（不知道成功率，给它机会）
                return (0 if is_new else 1, -rate)

            sorted_tasks = sorted(clean_tasks, key=_sort_key)
            selected = sorted_tasks[:task_count]

            # 生成优先级说明（供 Supervisor 了解决策依据）
            priority_lines = []
            for t in selected[:5]:
                url = str(t.get("来源链接", "") or "")[:50]
                profile = memory.get_site_profile(t.get("来源链接", ""))
                rate = profile.get("success_rate", 0.5)
                strategy = profile.get("best_strategy", "dom")
                priority_lines.append(f"{url} → 成功率{rate:.0%}，推荐策略={strategy}")
            priority_info = "\n".join(priority_lines)

            self.log(f"已选 {len(selected)}/{len(all_tasks)} 条任务（过滤黑名单 {blacklisted_count} 条）", "OK")
            return AgentMessage.result(
                self.name, message.from_agent,
                success=True,
                tasks=selected,
                skipped_blacklisted=blacklisted_count,
                today_success_by_site=today_success,
                priority_info=priority_info,
            )

        except Exception as exc:
            self.log(f"调度失败: {exc}", "ERROR")
            return AgentMessage.error(self.name, message.from_agent, str(exc)[:200])

    def _get_progress(self, message: AgentMessage) -> AgentMessage:
        """查询今日各站点完成进度。"""
        try:
            from sync_reporting_workbook import sync_reporting_workbook
            from backlink_state import STATUS_SUCCESS
            from datetime import datetime

            today_str = datetime.now().strftime("%Y-%m-%d")
            sync_result = sync_reporting_workbook() or {}
            status_rows = sync_result.get("status_rows", [])
            targets = [t for t in sync_result.get("targets", []) if t.get("是否启用") == "是"]

            today_success_by_site: dict = {}
            for row in status_rows:
                if row.get("状态") == STATUS_SUCCESS:
                    site = str(row.get("目标站标识", "") or "")
                    today_success_by_site[site] = today_success_by_site.get(site, 0) + 1

            site_progress = []
            all_done = True
            for target in targets:
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

            return AgentMessage.result(
                self.name, message.from_agent,
                success=True,
                all_sites_done=all_done,
                site_progress=site_progress,
            )
        except Exception as exc:
            return AgentMessage.error(self.name, message.from_agent, str(exc)[:200])
