"""
agents/supervisor_agent.py
===========================
Supervisor Agent - Multi-Agent 系统总协调器

职责（就像一个团队主管）：
  - 每天启动后制定工作计划
  - 向 SchedulerAgent 请求任务列表
  - 把任务分批下发给 ExecutorAgent
  - 接收执行结果，判断是否继续或结束
  - 连续失败时请求 AnalyzerAgent 分析原因
  - 所有站点完成后，请求 AnalyzerAgent 发送日报

工作循环：
  ① supervisor 问 scheduler：今天有哪些任务？
  ↓
  ② scheduler 返回有序任务列表
  ↓
  ③ supervisor 把任务交给 executor：去发帖！
  ↓
  ④ executor 返回结果：成功 X 条，失败 Y 条
  ↓
  ⑤ supervisor 问 scheduler：今天的目标完成了吗？
  ↓（如果未完成）
  ↑ 回到步骤①（再拿一批任务）
  ↓（如果完成了）
  ⑥ supervisor 请求 analyzer 发飞书日报
  ⑦ 记录本次运行摘要，结束
"""

import sys
import os
import time
from typing import Optional
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base_agent import AgentMessage, BaseAgent
from agents.scheduler_agent import SchedulerAgent
from agents.executor_agent import ExecutorAgent
from agents.analyzer_agent import AnalyzerAgent


# 最多执行多少轮（防止无限循环）
DEFAULT_MAX_ROUNDS = 10
# 连续多少轮没有新增成功就停止
DEFAULT_MAX_IDLE_ROUNDS = 3


class SupervisorAgent(BaseAgent):
    """
    Multi-Agent 总协调器。

    内部持有并直接调用三个子 Agent（通过消息传递）：
      - SchedulerAgent：获取任务和查询进度
      - ExecutorAgent：执行发帖
      - AnalyzerAgent：分析失败 + 发日报
    """

    def __init__(self, config_path: str = "config.json", dry_run: bool = False):
        super().__init__(
            name="SupervisorAgent",
            role_description="总协调器，负责每日发帖任务的全局规划、进度追踪和 Agent 间协调",
            model="gemini-2.0-flash",
            config_path=config_path,
        )
        self._dry_run = dry_run
        scheduler_cfg = self._config.get("multi_agent", {})
        self._max_rounds = int(scheduler_cfg.get("max_rounds", DEFAULT_MAX_ROUNDS) or DEFAULT_MAX_ROUNDS)
        self._max_idle_rounds = int(scheduler_cfg.get("max_idle_rounds", DEFAULT_MAX_IDLE_ROUNDS) or DEFAULT_MAX_IDLE_ROUNDS)
        self._tasks_per_round = int(scheduler_cfg.get("tasks_per_round", 5) or 5)

        # 初始化子 Agent（直接持有实例）
        self._scheduler = SchedulerAgent(config_path)
        self._executor = ExecutorAgent(config_path)
        self._analyzer = AnalyzerAgent(config_path)

    def handle_message(self, message: AgentMessage) -> AgentMessage:
        """Supervisor 通常不被外部调用，而是主动发起工作。"""
        return AgentMessage.result(
            self.name, message.from_agent,
            success=True,
            message="Supervisor 已运行，请调用 run_daily_session() 启动工作",
        )

    def _send_to(self, agent: BaseAgent, msg_type: str, action: str, **payload) -> AgentMessage:
        """
        向子 Agent 发送任务消息，并返回响应。
        这是 Supervisor 与子 Agent 通信的统一方法。
        """
        message = AgentMessage.task(
            from_agent=self.name,
            to_agent=agent.name,
            action=action,
            **payload,
        )
        self.log(f"→ [{agent.name}] {action}", "INFO")
        response = agent.handle_message(message)
        status = "OK" if response.payload.get("success", False) else "WARN"
        self.log(f"← [{agent.name}] {response.type} | {str(response.payload.get('message', ''))[:80]}", status)
        return response

    def run_daily_session(self) -> dict:
        """
        完整的每日 Multi-Agent 工作会话。

        返回运行摘要 dict。
        """
        from datetime import datetime

        print("\n" + "=" * 60)
        print(f"🎯 Multi-Agent 系统启动 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        print(f"   协调器: {self.name}")
        print(f"   子Agent: {self._scheduler.name} | {self._executor.name} | {self._analyzer.name}")
        print(f"   模式: {'DRY RUN' if self._dry_run else '正式发帖'}")
        print("=" * 60)

        total_success = 0
        total_failed = 0
        all_failed_details = []
        stop_reason = "达到最大轮次"
        idle_rounds = 0

        for round_idx in range(1, self._max_rounds + 1):
            print(f"\n{'─' * 50}")
            print(f"🔄 第 {round_idx}/{self._max_rounds} 轮")
            self.log(f"本轮任务数上限: {self._tasks_per_round}")

            # ── 步骤 1：向 Scheduler 请求任务 ──────────────────────────
            sched_resp = self._send_to(
                self._scheduler, "task", "select_tasks",
                task_count=self._tasks_per_round,
            )
            if sched_resp.type == "error":
                self.log(f"Scheduler 异常，跳过本轮: {sched_resp.payload.get('error', '')}", "ERROR")
                idle_rounds += 1
                if idle_rounds >= self._max_idle_rounds:
                    stop_reason = "Scheduler 连续异常"
                    break
                continue

            tasks = sched_resp.payload.get("tasks", [])
            if not tasks:
                stop_reason = "今日无可用任务"
                break

            # ── 步骤 2：向 Executor 下发任务 ──────────────────────────
            if self._dry_run:
                self.log(f"[DRY RUN] 模拟执行 {len(tasks)} 条任务（不实际发帖）", "WARN")
                exec_resp = AgentMessage.result(
                    self._executor.name, self.name,
                    success=True,
                    success_count=0,
                    failed_count=0,
                    elapsed_seconds=0,
                    message="[DRY RUN] 模拟执行",
                    success_details=[],
                    failed_details=[],
                )
            else:
                exec_resp = self._send_to(
                    self._executor, "task", "execute_batch",
                    tasks=tasks,
                    task_count=self._tasks_per_round,
                )

            if exec_resp.type == "error":
                self.log(f"Executor 异常: {exec_resp.payload.get('error', '')}", "ERROR")
                idle_rounds += 1
                if idle_rounds >= self._max_idle_rounds:
                    stop_reason = "Executor 连续异常"
                    break
                continue

            # 汇总本轮结果
            round_success = int(exec_resp.payload.get("success_count", 0) or 0)
            round_failed = int(exec_resp.payload.get("failed_count", 0) or 0)
            total_success += round_success
            total_failed += round_failed
            all_failed_details.extend(exec_resp.payload.get("failed_details", []))

            print(f"  💯 本轮: 新增成功 {round_success}，失败 {round_failed} | 累计成功 {total_success}")

            # 连续空轮检测
            if round_success == 0:
                idle_rounds += 1
                if idle_rounds >= self._max_idle_rounds:
                    stop_reason = f"连续 {idle_rounds} 轮无新增成功，任务池耗尽"
                    break
            else:
                idle_rounds = 0

            # ── 步骤 3：查询进度，判断是否完成 ───────────────────────
            progress_resp = self._send_to(self._scheduler, "task", "get_progress")
            if progress_resp.type != "error":
                all_done = progress_resp.payload.get("all_sites_done", False)
                if all_done:
                    stop_reason = "所有站点完成今日目标"
                    break

            time.sleep(1)  # 轮间间隔

        # ── 步骤 4：失败分析（如果有失败记录）────────────────────────
        suggestions = []
        if all_failed_details:
            analyze_resp = self._send_to(
                self._analyzer, "task", "analyze_failures",
                failed_details=all_failed_details[:20],  # 最多分析前 20 条
            )
            if analyze_resp.type != "error":
                suggestions = analyze_resp.payload.get("suggestions", [])

        # ── 步骤 5：请求 Analyzer 发送飞书日报 ───────────────────────
        report_resp = self._send_to(
            self._analyzer, "task", "generate_report",
            stop_reason=stop_reason,
            total_success=total_success,
            total_failed=total_failed,
            suggestions=suggestions,
        )

        report_sent = report_resp.payload.get("report_sent", False) if report_resp.type != "error" else False

        # 打印最终摘要
        print(f"\n{'=' * 60}")
        print(f"🏁 Multi-Agent 系统结束: {stop_reason}")
        print(f"   本次成功: {total_success} | 失败: {total_failed}")
        print(f"   飞书日报: {'已发送 ✅' if report_sent else '未发送'}")
        if suggestions:
            print("   优化建议:")
            for s in suggestions[:3]:
                print(f"     • {s}")
        print("=" * 60)

        return {
            "ok": True,
            "rounds": min(round_idx, self._max_rounds),
            "total_success": total_success,
            "total_failed": total_failed,
            "stop_reason": stop_reason,
            "report_sent": report_sent,
            "suggestions": suggestions,
            "dry_run": self._dry_run,
        }
