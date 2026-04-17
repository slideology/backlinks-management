"""
agents/executor_agent.py
=========================
Executor Agent - 专注浏览器发帖操作

职责（就像一个专注执行的操作员）：
  - 接收 Supervisor 分配的任务列表
  - 调用 form_automation_local.run_once() 执行实际发帖
  - 完成后将结果（成功/失败详情）上报给 Supervisor
  - 完成后更新站点记忆（AgentMemory.record_result）

接收消息 (type="task")：
  { "action": "execute_batch", "tasks": [...], "task_count": 5 }

返回消息 (type="result")：
  { "success": True, "success_count": 3, "failed_count": 2, "details": {...} }
"""

import sys
import os
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base_agent import AgentMessage, BaseAgent, resolve_multi_agent_model


class ExecutorAgent(BaseAgent):
    """
    发帖执行 Agent。

    专门负责浏览器操作和发帖执行，
    成功/失败结果都会写入 AgentMemory，让系统越来越聪明。
    """

    def __init__(self, config_path: str = "config.json"):
        super().__init__(
            name="ExecutorAgent",
            role_description="发帖执行专员，负责浏览器操作，执行实际的外链发布任务",
            model=resolve_multi_agent_model(config_path, "executor", "gemini-flash-lite-latest"),
            config_path=config_path,
        )
        self._memory = None

    def _get_memory(self):
        if self._memory is None:
            from agent_memory import AgentMemory
            self._memory = AgentMemory()
        return self._memory

    def handle_message(self, message: AgentMessage) -> AgentMessage:
        """处理来自 Supervisor 的执行请求。"""
        action = message.payload.get("action", "")
        self.log(f"收到任务: {action}")

        if action == "execute_batch":
            return self._execute_batch(message)
        else:
            return AgentMessage.error(
                self.name, message.from_agent,
                f"未知指令 '{action}'，支持: execute_batch"
            )

    def _execute_batch(self, message: AgentMessage) -> AgentMessage:
        """
        批量执行发帖任务。

        流程：
          ① 接收 tasks 列表
          ② 调用 run_once() 执行
          ③ 将每条结果（成功/失败/所用策略）写入 AgentMemory
          ④ 返回汇总结果给 Supervisor
        """
        tasks = message.payload.get("tasks", [])
        task_count = int(message.payload.get("task_count", len(tasks)) or len(tasks))
        tasks_to_run = tasks[:max(1, task_count)]

        if not tasks_to_run:
            return AgentMessage.result(
                self.name, message.from_agent,
                success=True,
                success_count=0,
                failed_count=0,
                elapsed_seconds=0,
                message="没有可执行的任务",
            )

        self.log(f"开始执行 {len(tasks_to_run)} 条任务...", "THINK")
        start_time = time.time()

        try:
            from form_automation_local import run_once
            batch_result = run_once(selected_tasks=tasks_to_run, send_report=False) or {}
        except Exception as exc:
            self.log(f"run_once 异常: {exc}", "ERROR")
            return AgentMessage.error(self.name, message.from_agent, str(exc)[:200])

        elapsed = round(time.time() - start_time, 1)
        success_list = batch_result.get("success", [])
        failed_list = batch_result.get("failed", [])

        # 写入 AgentMemory（让系统记住每个站点的结果）
        memory = self._get_memory()
        for item in success_list:
            url = str(item.get("url", "") or "")
            if item.get("memory_recorded"):
                continue
            strategy = "vision" if item.get("used_vision") else "dom"
            memory.record_result(url, success=True, strategy=strategy, elapsed_seconds=elapsed / max(1, len(tasks_to_run)))

        for item in failed_list:
            url = str(item.get("url", "") or "")
            if item.get("memory_recorded"):
                continue
            category = str(item.get("diagnostic_category", "") or "")
            strategy = "vision" if item.get("used_vision") else "dom"
            memory.record_result(url, success=False, strategy=strategy, failure_reason=category)

            # 如果 Agent 决策是黑名单，标记
            if category in {"agent_decided_skip", "vision_skipped_no_comment_signal"}:
                agent_reason = str(item.get("agent_reason", "") or "")
                if "mark_blacklist" in str(item.get("agent_decision", "") or ""):
                    memory.mark_blacklist(url, agent_reason or "Executor Agent 标记黑名单")

        self.log(f"执行完成：成功 {len(success_list)} 条，失败 {len(failed_list)} 条，耗时 {elapsed}s", "OK")

        return AgentMessage.result(
            self.name, message.from_agent,
            success=True,
            success_count=len(success_list),
            failed_count=len(failed_list),
            elapsed_seconds=elapsed,
            success_details=success_list[:5],
            failed_details=failed_list[:5],
            message=f"批量执行完成：成功 {len(success_list)}，失败 {len(failed_list)}，耗时 {elapsed}s",
        )
