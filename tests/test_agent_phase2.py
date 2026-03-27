"""
test_agent_memory.py & test_agent_tools_dispatch.py
=====================================================
第二阶段 Agent 单元测试

覆盖：
  - AgentMemory：记录结果、黑名单、成功率计算、域名提取
  - dispatch_tool_call：工具分发、未知工具处理
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# 确保项目根目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_memory import AgentMemory, _extract_domain


class TestExtractDomain(unittest.TestCase):
    """测试 URL 域名提取工具函数"""

    def test_basic_url(self):
        self.assertEqual(_extract_domain("https://example.com/blog/post"), "example.com")

    def test_www_prefix_removed(self):
        self.assertEqual(_extract_domain("https://www.example.com/page"), "example.com")

    def test_subdomain_kept(self):
        self.assertEqual(_extract_domain("https://news.example.com/post"), "news.example.com")

    def test_empty_url(self):
        result = _extract_domain("")
        self.assertIsInstance(result, str)

    def test_invalid_url(self):
        """无效 URL 不应崩溃，应返回字符串"""
        result = _extract_domain("not-a-valid-url")
        self.assertIsInstance(result, str)


class TestAgentMemory(unittest.TestCase):
    """测试 Agent 记忆模块核心功能"""

    def setUp(self):
        """每个测试前创建临时目录"""
        self.tmpdir = tempfile.mkdtemp()
        self.memory = AgentMemory(memory_dir=self.tmpdir)

    def test_new_site_has_default_profile(self):
        """新站点应有合理的默认档案"""
        profile = self.memory.get_site_profile("https://newsite.com")
        self.assertEqual(profile["domain"], "newsite.com")
        self.assertEqual(profile["attempts"], 0)
        # 新站点默认成功率 0.5（中性），允许继续尝试
        self.assertEqual(profile["success_rate"], 0.5)
        self.assertTrue(profile["is_worth_trying"])

    def test_record_success_increases_counters(self):
        """记录成功后，成功数和尝试数都应增加"""
        url = "https://example.com/blog"
        self.memory.record_result(url, success=True, strategy="dom", elapsed_seconds=5.0)
        profile = self.memory.get_site_profile(url)
        self.assertEqual(profile["attempts"], 1)
        self.assertEqual(profile["successes"], 1)
        self.assertEqual(profile["success_rate"], 1.0)

    def test_record_failure_increases_attempts_not_success(self):
        """记录失败后，只有尝试数增加，成功数不变"""
        url = "https://failsite.com/post"
        self.memory.record_result(url, success=False, strategy="dom", elapsed_seconds=8.0)
        profile = self.memory.get_site_profile(url)
        self.assertEqual(profile["attempts"], 1)
        self.assertEqual(profile["successes"], 0)
        self.assertEqual(profile["consecutive_failures"], 1)

    def test_consecutive_failures_tracking(self):
        """连续失败计数应正确递增"""
        url = "https://hardsite.com"
        for _ in range(3):
            self.memory.record_result(url, success=False)
        profile = self.memory.get_site_profile(url)
        self.assertEqual(profile["consecutive_failures"], 3)

    def test_success_resets_consecutive_failures(self):
        """成功后连续失败计数应归零"""
        url = "https://mixedsite.com"
        self.memory.record_result(url, success=False)
        self.memory.record_result(url, success=False)
        self.memory.record_result(url, success=True)
        profile = self.memory.get_site_profile(url)
        self.assertEqual(profile["consecutive_failures"], 0)

    def test_best_strategy_updates_to_highest_success_rate(self):
        """最佳策略应自动更新为成功率最高的那个"""
        url = "https://strategy-test.com"
        # DOM 失败 2 次
        self.memory.record_result(url, success=False, strategy="dom")
        self.memory.record_result(url, success=False, strategy="dom")
        # Vision 成功 2 次
        self.memory.record_result(url, success=True, strategy="vision")
        self.memory.record_result(url, success=True, strategy="vision")
        profile = self.memory.get_site_profile(url)
        self.assertEqual(profile["best_strategy"], "vision")

    def test_blacklist_marks_site(self):
        """黑名单标记应正确记录"""
        url = "https://blocked.com"
        self.memory.mark_blacklist(url, reason="评论区已关闭")
        self.assertTrue(self.memory.is_blacklisted(url))
        profile = self.memory.get_site_profile(url)
        self.assertFalse(profile["is_worth_trying"])  # 黑名单站点不值得尝试

    def test_non_blacklisted_site(self):
        """未加入黑名单的站点 is_blacklisted 应返回 False"""
        self.assertFalse(self.memory.is_blacklisted("https://clean-site.com"))

    def test_memory_persists_to_disk(self):
        """记录的数据应被持久化到磁盘"""
        url = "https://persist-test.com"
        self.memory.record_result(url, success=True)
        # 重新初始化 memory（模拟重启）
        new_memory = AgentMemory(memory_dir=self.tmpdir)
        profile = new_memory.get_site_profile(url)
        # 数据应从磁盘重新加载
        self.assertEqual(profile["attempts"], 1)
        self.assertEqual(profile["successes"], 1)

    def test_stats_summary(self):
        """统计摘要应准确反映总体情况"""
        self.memory.record_result("https://site-a.com", success=True)
        self.memory.record_result("https://site-a.com", success=True)
        self.memory.record_result("https://site-b.com", success=False)
        stats = self.memory.get_stats_summary()
        self.assertEqual(stats["total_sites_tracked"], 2)
        self.assertEqual(stats["total_attempts"], 3)
        self.assertEqual(stats["total_successes"], 2)
        self.assertAlmostEqual(stats["overall_success_rate"], 0.67, places=1)

    def test_get_recommended_strategy_defaults_to_dom(self):
        """新站点（无历史）推荐策略应为 dom（最保守）"""
        strategy = self.memory.get_recommended_strategy("https://brand-new-site.com")
        self.assertEqual(strategy, "dom")

    def test_export_context_for_agent(self):
        """导出 Agent 上下文应包含所有站点信息"""
        self.memory.record_result("https://site-a.com", success=True)
        self.memory.mark_blacklist("https://blocked.com", "评论关闭")
        context = self.memory.export_context_for_agent(
            ["https://site-a.com", "https://blocked.com", "https://new-site.com"]
        )
        self.assertIn("site-a.com", context)
        self.assertIn("⛔ 黑名单", context)
        self.assertIn("🆕 新站点", context)

    def test_is_worth_trying_with_low_success_rate(self):
        """成功率过低的站点应被标记为不值得尝试"""
        url = "https://low-success.com"
        # 10 次全失败
        for _ in range(10):
            self.memory.record_result(url, success=False)
        profile = self.memory.get_site_profile(url)
        # 成功率 0%，连续失败 10 次：不值得继续尝试
        self.assertFalse(profile["is_worth_trying"])

    def test_is_worth_trying_with_high_consecutive_failures(self):
        """连续失败超过 5 次的站点应被标记为不值得尝试"""
        url = "https://keep-failing.com"
        for _ in range(5):
            self.memory.record_result(url, success=False)
        profile = self.memory.get_site_profile(url)
        self.assertFalse(profile["is_worth_trying"])


class TestDispatchToolCall(unittest.TestCase):
    """测试 agent_tools.dispatch_tool_call 工具分发"""

    def test_unknown_tool_returns_error(self):
        """调用未知工具应返回错误而不是崩溃"""
        from agent_tools import dispatch_tool_call
        result = dispatch_tool_call("nonexistent_tool", {})
        self.assertFalse(result.get("ok", True))
        self.assertIn("nonexistent_tool", result.get("message", ""))

    def test_known_tools_exist_in_schema(self):
        """所有 TOOL_DECLARATIONS 中的工具名必须在 TOOL_FUNCTIONS 中有对应函数"""
        from agent_tools import TOOL_DECLARATIONS, TOOL_FUNCTIONS
        for decl in TOOL_DECLARATIONS:
            tool_name = decl["name"]
            self.assertIn(tool_name, TOOL_FUNCTIONS, f"工具 {tool_name} 在 TOOL_DECLARATIONS 中声明但未在 TOOL_FUNCTIONS 中实现")

    def test_tool_declarations_have_required_fields(self):
        """每个工具声明都必须有 name 和 description"""
        from agent_tools import TOOL_DECLARATIONS
        for decl in TOOL_DECLARATIONS:
            self.assertIn("name", decl)
            self.assertIn("description", decl)
            self.assertTrue(len(decl["description"]) > 10, f"工具 {decl['name']} 的描述太短")


if __name__ == "__main__":
    unittest.main()
