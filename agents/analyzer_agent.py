"""
agents/analyzer_agent.py
=========================
Analyzer Agent - 负责结果分析、日报生成和优化建议

职责（就像一个数据分析师）：
  - 汇总今日所有发帖结果（成功/失败统计）
  - 结合 AgentMemory 分析哪些站点成功率在下降
  - 生成可读性高的飞书日报（调用 webhook_sender）
  - 提供下次运行的优化建议（如哪些站点该加黑名单）

接收消息 (type="task")：
  { "action": "generate_report", "stop_reason": "..." }
  { "action": "analyze_failures", "failed_details": [...] }

返回消息 (type="result")：
  { "success": True, "report_sent": True, "suggestions": ["..."] }
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.base_agent import AgentMessage, BaseAgent


class AnalyzerAgent(BaseAgent):
    """
    结果分析 Agent。

    核心功能：
      1. 汇总今日成功/失败数量
      2. 分析失败规律，给 Supervisor 提供优化建议
      3. 发送飞书日报
    """

    def __init__(self, config_path: str = "config.json"):
        super().__init__(
            name="AnalyzerAgent",
            role_description="数据分析师，负责汇总发帖结果、分析失败规律、生成飞书日报",
            model="gemini-2.0-flash",
            config_path=config_path,
        )
        self._memory = None

    def _get_memory(self):
        if self._memory is None:
            from agent_memory import AgentMemory
            self._memory = AgentMemory()
        return self._memory

    def handle_message(self, message: AgentMessage) -> AgentMessage:
        """处理来自 Supervisor 的分析/日报请求。"""
        action = message.payload.get("action", "")
        self.log(f"收到任务: {action}")

        if action == "generate_report":
            return self._generate_report(message)
        elif action == "analyze_failures":
            return self._analyze_failures(message)
        else:
            return AgentMessage.error(
                self.name, message.from_agent,
                f"未知指令 '{action}'，支持: generate_report / analyze_failures"
            )

    def _generate_report(self, message: AgentMessage) -> AgentMessage:
        """
        生成并发送飞书日报。
        报告标题格式：🤖 [Multi-Agent] 外链自动化日报 | site1:5 site2:8
        """
        stop_reason = str(message.payload.get("stop_reason", "") or "Agent 自主完成")
        total_success = int(message.payload.get("total_success", 0) or 0)
        total_failed = int(message.payload.get("total_failed", 0) or 0)
        suggestions = message.payload.get("suggestions", [])

        try:
            from sync_reporting_workbook import sync_reporting_workbook
            from backlink_state import STATUS_SUCCESS
            from webhook_sender import create_webhook_sender
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

            # 验证记忆统计
            memory = self._get_memory()
            memory_stats = memory.get_stats_summary()

            # 生成建议文本
            suggestion_text = ""
            if suggestions:
                suggestion_text = "\n\n**🔍 优化建议：**\n" + "\n".join(f"- {s}" for s in suggestions[:5])

            sender = create_webhook_sender()
            if not sender:
                self.log("未配置飞书 Webhook，跳过日报", "WARN")
                return AgentMessage.result(
                    self.name, message.from_agent,
                    success=True,
                    report_sent=False,
                    message="未配置飞书 Webhook",
                    today_success_by_site=today_success_by_site,
                )

            # 构建标题
            site_summary = " | ".join(
                f"{target.get('站点标识', '')}:{today_success_by_site.get(target.get('站点标识', ''), 0)}"
                for target in targets
            )
            title = f"🤖 [Multi-Agent] 外链自动化日报 | {site_summary}"

            # 构建站点行
            site_rows = []
            for target in targets:
                site_key = str(target.get("站点标识", "") or "")
                site_rows.append({
                    "site_key": site_key,
                    "today_success": today_success_by_site.get(site_key, 0),
                    "daily_goal": int(str(target.get("每日成功目标", 10) or 10)),
                    "run_success": total_success,
                    "run_failed": total_failed,
                })

            sent = sender.send_summary_report(title, {
                "sites": site_rows,
                "total_success": sum(today_success_by_site.values()),
                "total_failed": total_failed,
                "stop_reason": f"[Multi-Agent] {stop_reason}{suggestion_text}",
            })

            self.log(f"飞书日报{'发送成功' if sent else '发送失败'}", "OK" if sent else "ERROR")
            return AgentMessage.result(
                self.name, message.from_agent,
                success=True,
                report_sent=sent,
                today_success_by_site=today_success_by_site,
                memory_stats=memory_stats,
                message="日报发送完成",
            )
        except Exception as exc:
            self.log(f"日报发送异常: {exc}", "ERROR")
            return AgentMessage.error(self.name, message.from_agent, str(exc)[:200])

    def _analyze_failures(self, message: AgentMessage) -> AgentMessage:
        """
        分析失败列表，返回优化建议。
        使用 AI 识别失败模式，如"3 个站点都是评论关闭"。
        """
        failed_details = message.payload.get("failed_details", [])
        if not failed_details:
            return AgentMessage.result(
                self.name, message.from_agent,
                success=True,
                suggestions=["没有失败记录，系统运行良好！"],
            )

        # 统计失败类别
        category_counts: dict = {}
        for item in failed_details:
            cat = str(item.get("diagnostic_category", "unknown") or "unknown")
            category_counts[cat] = category_counts.get(cat, 0) + 1

        # 生成规则性建议（不需要调用 AI）
        suggestions = []
        for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
            if count >= 3:
                if "no_comment" in cat or "disabled" in cat:
                    suggestions.append(f"发现 {count} 个站点评论区关闭，建议将其加入黑名单")
                elif "captcha" in cat or "recaptcha" in cat:
                    suggestions.append(f"发现 {count} 个站点有验证码保护，建议标记黑名单")
                elif "timeout" in cat:
                    suggestions.append(f"发现 {count} 个站点响应超时，可能是网络问题，建议稍后重试")
                elif "login" in cat or "auth" in cat:
                    suggestions.append(f"发现 {count} 个站点要求登录，建议配置 SSO")
                else:
                    suggestions.append(f"失败类型 '{cat}' 出现 {count} 次，建议人工检查")

        if not suggestions:
            suggestions.append("失败较少且分散，暂无批量优化建议，继续观察")

        self.log(f"分析完成，生成 {len(suggestions)} 条建议", "OK")
        return AgentMessage.result(
            self.name, message.from_agent,
            success=True,
            suggestions=suggestions,
            category_stats=category_counts,
        )
