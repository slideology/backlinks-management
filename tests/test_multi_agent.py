"""
test_multi_agent.py
=====================
第三阶段 Multi-Agent 单元测试

覆盖：
  - AgentMessage：消息格式、快捷方法
  - BaseAgent：基本初始化、日志、配置加载
  - SupervisorAgent：消息路由、子 Agent 协调
  - SchedulerAgent：任务处理逻辑（Mock 飞书）
  - ExecutorAgent：发帖结果处理（Mock run_once）
  - AnalyzerAgent：失败分析、建议生成
  - detect_mode：自动模式检测
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

# 确保项目根目录和 agents/ 包可以被找到
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from agents.base_agent import AgentMessage, BaseAgent


# =====================================================================
# AgentMessage 测试
# =====================================================================

class TestAgentMessage(unittest.TestCase):
    """测试 Agent 间消息格式"""

    def test_task_factory(self):
        """task() 快捷方法应创建 type='task' 的消息"""
        msg = AgentMessage.task("supervisor", "scheduler", action="select_tasks", task_count=5)
        self.assertEqual(msg.from_agent, "supervisor")
        self.assertEqual(msg.to_agent, "scheduler")
        self.assertEqual(msg.type, "task")
        self.assertEqual(msg.payload["action"], "select_tasks")
        self.assertEqual(msg.payload["task_count"], 5)

    def test_result_factory(self):
        """result() 快捷方法应创建 type='result' 的消息，payload 包含 success 字段"""
        msg = AgentMessage.result("executor", "supervisor", success=True, success_count=3)
        self.assertEqual(msg.type, "result")
        self.assertTrue(msg.payload["success"])
        self.assertEqual(msg.payload["success_count"], 3)

    def test_error_factory(self):
        """error() 快捷方法应创建 type='error' 的消息"""
        msg = AgentMessage.error("scheduler", "supervisor", "飞书连接失败")
        self.assertEqual(msg.type, "error")
        self.assertEqual(msg.payload["error"], "飞书连接失败")

    def test_to_dict(self):
        """消息可以转换为字典（用于日志和传输）"""
        msg = AgentMessage.task("a", "b", action="test")
        d = msg.to_dict()
        self.assertIsInstance(d, dict)
        self.assertIn("from_agent", d)
        self.assertIn("to_agent", d)
        self.assertIn("type", d)
        self.assertIn("timestamp", d)

    def test_timestamp_auto_generated(self):
        """消息应自动生成时间戳"""
        msg = AgentMessage.task("a", "b", action="test")
        self.assertTrue(len(msg.timestamp) > 0)


# =====================================================================
# BaseAgent 测试
# =====================================================================

class TestBaseAgent(unittest.TestCase):
    """测试 Agent 基类"""

    def setUp(self):
        """创建临时 config.json 用于测试"""
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmpdir, "config.json")
        import json
        with open(self.config_path, "w") as f:
            json.dump({"test_setting": 42}, f)

    def test_base_agent_init(self):
        """BaseAgent 应该可以正确初始化"""
        # BaseAgent 是抽象类，但可以直接实例化（handle_message 会 raise）
        agent = BaseAgent(
            name="TestAgent",
            role_description="测试用",
            config_path=self.config_path,
        )
        self.assertEqual(agent.name, "TestAgent")

    def test_load_config_success(self):
        """正确路径的配置文件应被成功加载"""
        agent = BaseAgent("TestAgent", "测试", config_path=self.config_path)
        self.assertEqual(agent._config.get("test_setting"), 42)

    def test_load_config_missing_file(self):
        """找不到配置文件时应返回空 dict，不报错"""
        agent = BaseAgent("TestAgent", "测试", config_path="/nonexistent/config.json")
        self.assertIsInstance(agent._config, dict)

    def test_handle_message_raises_not_implemented(self):
        """未重写 handle_message 应抛出 NotImplementedError"""
        agent = BaseAgent("TestAgent", "测试", config_path=self.config_path)
        msg = AgentMessage.task("other", "TestAgent", action="test")
        with self.assertRaises(NotImplementedError):
            agent.handle_message(msg)


# =====================================================================
# AnalyzerAgent 测试（可以离线测试的部分）
# =====================================================================

class TestAnalyzerAgent(unittest.TestCase):
    """测试 AnalyzerAgent 失败分析功能"""

    def setUp(self):
        from agents.analyzer_agent import AnalyzerAgent
        self.agent = AnalyzerAgent(config_path="config.json")

    def test_analyze_failures_no_failures(self):
        """空失败列表应返回正常摘要，不报错"""
        msg = AgentMessage.task("supervisor", "analyzer", action="analyze_failures", failed_details=[])
        resp = self.agent.handle_message(msg)
        self.assertEqual(resp.type, "result")
        self.assertTrue(resp.payload.get("success"))
        self.assertIn("suggestions", resp.payload)

    def test_analyze_failures_with_recaptcha(self):
        """多个 reCAPTCHA 失败应生成黑名单建议"""
        failed_details = [
            {"url": f"https://site{i}.com", "diagnostic_category": "recaptcha_protected"}
            for i in range(4)
        ]
        msg = AgentMessage.task("supervisor", "analyzer", action="analyze_failures", failed_details=failed_details)
        resp = self.agent.handle_message(msg)
        self.assertEqual(resp.type, "result")
        suggestions = resp.payload.get("suggestions", [])
        # 4 个 reCAPTCHA 失败 → 应有建议
        self.assertTrue(len(suggestions) > 0)
        self.assertTrue(any("验证码" in s or "黑名单" in s for s in suggestions))

    def test_analyze_failures_with_comment_disabled(self):
        """多个评论区关闭失败应生成加黑名单建议"""
        failed_details = [
            {"url": f"https://site{i}.com", "diagnostic_category": "comment_disabled"}
            for i in range(3)
        ]
        msg = AgentMessage.task("supervisor", "analyzer", action="analyze_failures", failed_details=failed_details)
        resp = self.agent.handle_message(msg)
        suggestions = resp.payload.get("suggestions", [])
        self.assertTrue(any("评论" in s or "黑名单" in s for s in suggestions))

    def test_analyze_failures_unknown_action(self):
        """未知 action 应返回错误消息"""
        msg = AgentMessage.task("supervisor", "analyzer", action="unknown_action")
        resp = self.agent.handle_message(msg)
        self.assertEqual(resp.type, "error")


# =====================================================================
# SupervisorAgent 测试（Mock 子 Agent）
# =====================================================================

class TestSupervisorAgentBasic(unittest.TestCase):
    """测试 SupervisorAgent 的消息路由和子 Agent 协调（不实际调用 API）"""

    def setUp(self):
        from agents.supervisor_agent import SupervisorAgent
        self.supervisor = SupervisorAgent(dry_run=True)

    def test_supervisor_has_all_sub_agents(self):
        """SupervisorAgent 应持有 3 个子 Agent"""
        from agents.scheduler_agent import SchedulerAgent
        from agents.executor_agent import ExecutorAgent
        from agents.analyzer_agent import AnalyzerAgent
        self.assertIsInstance(self.supervisor._scheduler, SchedulerAgent)
        self.assertIsInstance(self.supervisor._executor, ExecutorAgent)
        self.assertIsInstance(self.supervisor._analyzer, AnalyzerAgent)

    def test_supervisor_dry_run_flag(self):
        """dry_run=True 时 Supervisor 的 _dry_run 应为 True"""
        self.assertTrue(self.supervisor._dry_run)

    def test_supervisor_handle_message_returns_result(self):
        """外部调用 handle_message 应返回 result 类型消息（不报错）"""
        msg = AgentMessage.task("external", "supervisor", action="status")
        resp = self.supervisor.handle_message(msg)
        self.assertEqual(resp.type, "result")


# =====================================================================
# detect_mode 测试
# =====================================================================

class TestDetectMode(unittest.TestCase):
    """测试 agent_runner.py 的模式自动检测"""

    def setUp(self):
        from agent_runner import detect_mode
        self.detect_mode = detect_mode

    def test_detect_multi_agent_when_enabled(self):
        """multi_agent.enabled=true → 返回 'multi_agent'"""
        config = {"multi_agent": {"enabled": True}, "agent": {"enabled": True}}
        self.assertEqual(self.detect_mode(config), "multi_agent")

    def test_detect_agent_when_multi_disabled(self):
        """multi_agent.enabled=false, agent.enabled=true → 返回 'agent'"""
        config = {"multi_agent": {"enabled": False}, "agent": {"enabled": True}}
        self.assertEqual(self.detect_mode(config), "agent")

    def test_detect_classic_when_all_disabled(self):
        """全部关闭 → 返回 'classic'"""
        config = {"multi_agent": {"enabled": False}, "agent": {"enabled": False}}
        self.assertEqual(self.detect_mode(config), "classic")

    def test_detect_classic_empty_config(self):
        """空配置 → 返回 'classic'（最安全的模式）"""
        self.assertEqual(self.detect_mode({}), "classic")


if __name__ == "__main__":
    unittest.main()
