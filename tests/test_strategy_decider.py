"""
test_strategy_decider.py
=========================
策略决策器单元测试

测试覆盖：
  - 规则决策（错误码命中映射表）
  - 规则决策（错误信息关键词命中）
  - 规则无法判断时的返回值
  - 当决策器关闭时返回默认策略
  - 辅助判断函数（should_try_vision / should_skip / is_blacklist 等）
  - AI 决策失败时的保守兜底逻辑
  - 决策日志写入
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# 确保项目根目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategy_decider import (
    STRATEGY_MARK_BLACKLIST,
    STRATEGY_RETRY_DOM,
    STRATEGY_SKIP,
    STRATEGY_TRY_SSO,
    STRATEGY_TRY_VISION,
    _rule_based_decide,
    decide_next_strategy,
    is_blacklist,
    should_retry_dom,
    should_skip,
    should_try_sso,
    should_try_vision,
)


class TestRuleBasedDecide(unittest.TestCase):
    """测试快速规则决策逻辑（不调用 AI）"""

    def test_dom_not_found_routes_to_vision(self):
        """DOM 找不到评论框 → 应建议尝试 Vision"""
        result = _rule_based_decide("dom_not_found", "Layer 1 未找到评论输入框")
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], STRATEGY_TRY_VISION)
        self.assertEqual(result["decision_source"], "rule_error_code")
        self.assertGreater(result["confidence"], 0.8)

    def test_vision_timeout_routes_to_skip(self):
        """Vision API 超时 → 应建议跳过（避免循环等待）"""
        result = _rule_based_decide("vision_api_error", "Request timed out")
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], STRATEGY_SKIP)

    def test_circuit_breaker_routes_to_skip(self):
        """Vision 熔断中 → 应建议跳过"""
        result = _rule_based_decide("vision_temporarily_paused", "Vision 熔断中，约 300 秒后恢复")
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], STRATEGY_SKIP)

    def test_login_keyword_routes_to_sso(self):
        """错误信息包含 'log in to comment' → 应建议尝试 SSO"""
        result = _rule_based_decide("dom_submit_failed", "页面提示 log in to comment")
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], STRATEGY_TRY_SSO)
        self.assertEqual(result["decision_source"], "rule_keyword")

    def test_must_login_keyword_routes_to_sso(self):
        """错误信息包含 'you must be logged in' → 应建议尝试 SSO（使用不在映射表的错误码）"""
        # 注意：必须用不在错误码映射表的 error_code，
        # 否则错误码规则会优先于关键词规则
        result = _rule_based_decide("page_load_ok", "you must be logged in to comment")
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], STRATEGY_TRY_SSO)

    def test_recaptcha_routes_to_skip(self):
        """检测到 reCAPTCHA 保护 → 应建议跳过"""
        result = _rule_based_decide("dom_submit_failed", "页面有 recaptcha 验证码保护")
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], STRATEGY_SKIP)

    def test_comments_closed_routes_to_skip(self):
        """评论区明确关闭 → 应建议跳过（标记黑名单）（使用不在映射表的错误码）"""
        # 注意：必须用不在错误码映射表的 error_code，
        # 否则错误码规则会优先于关键词规则
        result = _rule_based_decide("page_loaded", "comments are closed")
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], STRATEGY_MARK_BLACKLIST)

    def test_timeout_keyword_routes_to_retry_dom(self):
        """网络超时 → 应建议重试 DOM（可能是临时网络波动）"""
        result = _rule_based_decide("navigation_timeout", "net::err_timed_out 页面加载超时")
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], STRATEGY_RETRY_DOM)

    def test_unknown_error_returns_none(self):
        """未知错误码且无关键词 → 规则无法判断，返回 None（交给 AI）"""
        result = _rule_based_decide("completely_unknown_error", "some weird error")
        self.assertIsNone(result)

    def test_empty_error_code_falls_through_to_keyword(self):
        """空错误码时，仍会检查关键词"""
        result = _rule_based_decide("", "sign in to leave a comment")
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], STRATEGY_TRY_SSO)


class TestHelperFunctions(unittest.TestCase):
    """测试辅助判断函数"""

    def test_should_try_vision(self):
        self.assertTrue(should_try_vision({"action": STRATEGY_TRY_VISION}))
        self.assertFalse(should_try_vision({"action": STRATEGY_SKIP}))

    def test_should_try_sso(self):
        self.assertTrue(should_try_sso({"action": STRATEGY_TRY_SSO}))
        self.assertFalse(should_try_sso({"action": STRATEGY_TRY_VISION}))

    def test_should_retry_dom(self):
        self.assertTrue(should_retry_dom({"action": STRATEGY_RETRY_DOM}))
        self.assertFalse(should_retry_dom({"action": STRATEGY_SKIP}))

    def test_should_skip_covers_both_skip_and_blacklist(self):
        """should_skip 应同时覆盖 skip 和 mark_blacklist 两种策略"""
        self.assertTrue(should_skip({"action": STRATEGY_SKIP}))
        self.assertTrue(should_skip({"action": STRATEGY_MARK_BLACKLIST}))
        self.assertFalse(should_skip({"action": STRATEGY_TRY_VISION}))

    def test_is_blacklist_only_marks_blacklist(self):
        """is_blacklist 只对 mark_blacklist 策略返回 True"""
        self.assertTrue(is_blacklist({"action": STRATEGY_MARK_BLACKLIST}))
        self.assertFalse(is_blacklist({"action": STRATEGY_SKIP}))
        self.assertFalse(is_blacklist({"action": STRATEGY_TRY_VISION}))


class TestDecideNextStrategy(unittest.TestCase):
    """测试主决策函数 decide_next_strategy（含配置加载）"""

    def _make_config(self, tmpdir: str, enabled: bool = True) -> str:
        """生成临时 config.json 文件"""
        config_path = os.path.join(tmpdir, "config.json")
        config = {
            "strategy_decider": {
                "enabled": enabled,
                "model": "gemini-2.0-flash",
                "request_timeout_seconds": 5,
                "debug_dir": os.path.join(tmpdir, "artifacts/strategy_decider"),
                "confidence_threshold": 0.6,
                "rule_based_fallback": True,
            }
        }
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f)
        return config_path

    def test_rule_hit_does_not_call_ai(self):
        """明确规则命中时，不应调用 AI（节省成本）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._make_config(tmpdir)
            # dom_not_found 是明确规则：直接返回 try_vision
            result = decide_next_strategy(
                error_code="dom_not_found",
                error_message="未找到评论输入框",
                site_url="https://example.com/blog",
                screenshot_bytes=None,
                config_path=config_path,
            )
            self.assertEqual(result["action"], STRATEGY_TRY_VISION)
            self.assertEqual(result["decision_source"], "rule_error_code")

    def test_disabled_decider_returns_default(self):
        """决策器关闭时，应返回默认策略（try_vision）而不调用 AI"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._make_config(tmpdir, enabled=False)
            result = decide_next_strategy(
                error_code="anything",
                error_message="anything",
                site_url="https://example.com",
                config_path=config_path,
            )
            self.assertEqual(result["action"], STRATEGY_TRY_VISION)
            self.assertEqual(result["decision_source"], "disabled_fallback")

    def test_ai_failure_falls_back_to_conservative_skip(self):
        """AI 调用失败时，应保守跳过（避免无效消耗）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._make_config(tmpdir)
            # 使用未知错误码触发 AI 决策路径，同时 patch AI 调用使其失败
            with patch("strategy_decider._ai_decide", return_value=None):
                result = decide_next_strategy(
                    error_code="completely_unknown_xyz",
                    error_message="some weird exotic error",
                    site_url="https://exotic-site.com",
                    config_path=config_path,
                )
            self.assertEqual(result["action"], STRATEGY_SKIP)
            self.assertEqual(result["decision_source"], "conservative_fallback")

    def test_low_confidence_ai_result_falls_back(self):
        """AI 返回低置信度结果时，应保守跳过"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._make_config(tmpdir)
            low_confidence_result = {
                "action": STRATEGY_TRY_VISION,
                "reason": "不太确定",
                "confidence": 0.3,  # 低于 threshold 0.6
                "decision_source": "ai",
            }
            with patch("strategy_decider._ai_decide", return_value=low_confidence_result):
                result = decide_next_strategy(
                    error_code="completely_unknown_xyz",
                    error_message="some exotic error",
                    site_url="https://exotic-site.com",
                    config_path=config_path,
                )
            self.assertEqual(result["action"], STRATEGY_SKIP)
            self.assertEqual(result["decision_source"], "conservative_fallback")

    def test_decision_log_is_written(self):
        """决策结果应该被记录到日志文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = self._make_config(tmpdir)
            decide_next_strategy(
                error_code="dom_not_found",
                error_message="未找到评论框",
                site_url="https://test-logging.com",
                config_path=config_path,
            )
            # 检查日志目录是否有文件生成
            log_dir = Path(tmpdir) / "artifacts" / "strategy_decider"
            log_files = list(log_dir.glob("*.jsonl"))
            self.assertTrue(len(log_files) > 0, "应该生成了决策日志文件")

            # 检查日志内容
            log_content = log_files[0].read_text(encoding="utf-8")
            log_record = json.loads(log_content.strip())
            self.assertEqual(log_record["site_url"], "https://test-logging.com")
            self.assertEqual(log_record["error_code"], "dom_not_found")
            self.assertIn("action", log_record["decision"])


if __name__ == "__main__":
    unittest.main()
