import unittest
from unittest.mock import patch

from browser_cdp import ensure_allowed_cdp_url, merge_browser_config
from form_automation_local import (
    DEFAULT_CONTACT_EMAIL,
    _build_task_failure_updates,
    _detect_hard_blocker,
    _frame_scan_priority,
    _is_blogger_comment_url,
    _is_irrelevant_frame_url,
    _preprobe_page_for_generation,
    _is_submission_context_reset_error,
    _page_has_comment_signals,
    _target_presence_tokens,
    _should_use_vision_fallback,
    _verify_post_success,
    resolve_runtime_link_format,
)


class FormAutomationLocalTests(unittest.TestCase):
    @patch("form_automation_local._deep_scroll_to_bottom")
    @patch("form_automation_local.try_dismiss_overlays")
    @patch("form_automation_local._fast_navigate_for_commenting", return_value={"partial_navigation": False, "navigation_warning": ""})
    def test_preprobe_page_for_generation_stops_on_hard_blocker(
        self,
        _mock_nav,
        _mock_dismiss,
        _mock_scroll,
    ):
        class FakeLocator:
            def all_inner_texts(self):
                return ["Please verify you are human before continuing."]

        class FakePage:
            frames = []

            def locator(self, selector):
                if selector == "body":
                    return FakeLocator()
                raise AssertionError(f"unexpected selector: {selector}")

        meta = _preprobe_page_for_generation(
            FakePage(),
            "https://example.com/post",
            15000,
            "classic_dom",
            "dom",
        )

        self.assertFalse(meta["ok"])
        self.assertEqual(meta["diagnostic_category"], "hard_blocker")

    @patch("form_automation_local._deep_scroll_to_bottom")
    @patch("form_automation_local.try_dismiss_overlays")
    @patch("form_automation_local._fast_navigate_for_commenting", return_value={"partial_navigation": False, "navigation_warning": ""})
    def test_preprobe_page_for_generation_stops_when_comment_signal_missing(
        self,
        _mock_nav,
        _mock_dismiss,
        _mock_scroll,
    ):
        class FakeLocator:
            def __init__(self, texts=None):
                self._texts = texts or [""]

            def count(self):
                return 0

            def all_inner_texts(self):
                return self._texts

        class FakePage:
            frames = []

            def locator(self, selector):
                if selector == "body":
                    return FakeLocator(["plain body without discussion"])
                return FakeLocator()

        meta = _preprobe_page_for_generation(
            FakePage(),
            "https://example.com/post",
            15000,
            "classic_dom",
            "dom",
        )

        self.assertFalse(meta["ok"])
        self.assertEqual(meta["diagnostic_category"], "comment_signal_missing")

    def test_should_use_vision_when_dom_never_found_comment_box(self):
        self.assertTrue(_should_use_vision_fallback("Layer 1: 主页面及所有嵌套 iframe 中均未找到任何评论输入框"))

    def test_should_use_vision_when_submit_button_missing(self):
        self.assertTrue(_should_use_vision_fallback("填写了评论内容，但没有找到可以点击的提交按钮。"))

    def test_should_not_use_vision_after_dom_submit_attempt(self):
        self.assertFalse(_should_use_vision_fallback("填写了评论且点击了提交，但页面没有出现成功提示词或发生跳转重定向。"))

    def test_build_task_failure_updates_normalizes_keys(self):
        updates = _build_task_failure_updates(
            {
                "来源链接": "[{'link': 'https://Example.com/post?utm_source=x', 'text': 'https://Example.com/post?utm_source=x', 'type': 'url'}]",
                "来源标题": "Example",
                "根域名": "example.com",
                "页面评分": "25",
            },
            {
                "site_key": "bearclicker.net",
                "url": "https://bearclicker.net/",
            },
            "运行时异常",
            "2026-03-23 11:15:00",
        )

        self.assertEqual(updates["来源链接"], "https://example.com/post")
        self.assertEqual(updates["目标站标识"], "bearclicker.net")
        self.assertEqual(updates["状态"], "待重试")
        self.assertEqual(updates["最近失败原因"], "运行时异常")
        self.assertEqual(updates["执行模式"], "classic_dom")
        self.assertEqual(updates["推荐策略"], "dom")
        self.assertEqual(updates["最近失败分类"], "other_failure")

    def test_is_blogger_comment_url_matches_blogger_frame(self):
        self.assertTrue(_is_blogger_comment_url("https://www.blogger.com/comment/frame/123"))
        self.assertTrue(_is_blogger_comment_url("https://www.blogblog.com/comment/frame/123"))
        self.assertFalse(_is_blogger_comment_url("https://example.com/comment/frame/123"))

    def test_is_irrelevant_frame_url_filters_ad_iframes(self):
        self.assertTrue(_is_irrelevant_frame_url("https://pagead2.googlesyndication.com/pagead/s/cookie_push_only.html"))
        self.assertFalse(_is_irrelevant_frame_url("https://www.blogger.com/comment/frame/123"))

    def test_frame_scan_priority_prefers_comment_frames(self):
        class FakeFrame:
            def __init__(self, url):
                self.url = url

        self.assertLess(
            _frame_scan_priority(FakeFrame("https://www.blogger.com/comment/frame/123")),
            _frame_scan_priority(FakeFrame("https://example.com/embed/widget")),
        )

    def test_submission_context_reset_error_matches_navigation_style_errors(self):
        self.assertTrue(
            _is_submission_context_reset_error(
                "Locator.all_inner_texts: Target page, context or browser has been closed"
            )
        )
        self.assertTrue(
            _is_submission_context_reset_error(
                "Execution context was destroyed, most likely because of a navigation"
            )
        )
        self.assertFalse(_is_submission_context_reset_error("timed out"))

    def test_target_presence_tokens_include_domain_and_anchor_text(self):
        tokens = _target_presence_tokens("https://www.bearclicker.net/", "Bear Clicker")
        self.assertIn("bearclicker.net", tokens)
        self.assertIn("bear clicker", tokens)

    def test_page_has_comment_signals_matches_comment_text(self):
        class FakeLocator:
            def __init__(self, count_value=0, texts=None):
                self._count_value = count_value
                self._texts = texts or []

            def count(self):
                return self._count_value

            def all_inner_texts(self):
                return self._texts

        class FakePage:
            def locator(self, selector):
                if selector == "body":
                    return FakeLocator(texts=["Leave a reply below"])
                return FakeLocator()

        ok, reason = _page_has_comment_signals(FakePage())
        self.assertTrue(ok)
        self.assertIn("评论区提示词", reason)

    def test_page_has_comment_signals_skips_closed_comments(self):
        class FakeLocator:
            def __init__(self, count_value=0, texts=None):
                self._count_value = count_value
                self._texts = texts or []

            def count(self):
                return self._count_value

            def all_inner_texts(self):
                return self._texts

        class FakePage:
            def locator(self, selector):
                if selector == "body":
                    return FakeLocator(texts=["Comments are closed"])
                return FakeLocator()

        ok, reason = _page_has_comment_signals(FakePage())
        self.assertFalse(ok)
        self.assertIn("评论已关闭", reason)

    def test_page_has_comment_signals_matches_comment_iframe(self):
        class FakeLocator:
            def __init__(self, count_value=0, texts=None):
                self._count_value = count_value
                self._texts = texts or []

            def count(self):
                return self._count_value

            def all_inner_texts(self):
                return self._texts

        class FakeFrame:
            def __init__(self, url):
                self.url = url

        class FakePage:
            frames = [FakeFrame("https://comments.example.com/embed/reply")]

            def locator(self, selector):
                if selector == "body":
                    return FakeLocator(texts=[""])
                return FakeLocator()

        ok, reason = _page_has_comment_signals(FakePage())
        self.assertTrue(ok)
        self.assertIn("iframe", reason)

    def test_detect_hard_blocker_matches_cloudflare_challenge_frame(self):
        class FakeLocator:
            def __init__(self, texts=None):
                self._texts = texts or [""]

            def all_inner_texts(self):
                return self._texts

        class FakeFrame:
            def __init__(self, url):
                self.url = url

        class FakePage:
            frames = [FakeFrame("https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/orchestrate")]

            def locator(self, selector):
                if selector == "body":
                    return FakeLocator()
                raise AssertionError(f"unexpected selector: {selector}")

        blocked, reason = _detect_hard_blocker(FakePage())
        self.assertTrue(blocked)
        self.assertIn("Cloudflare", reason)

    @patch("form_automation_local._detect_submission_side_effect", return_value="提交后页面中已看不到原评论输入框")
    def test_verify_post_success_accepts_submission_side_effect(self, mock_side_effect):
        class FakeBody:
            def all_inner_texts(self):
                return [""]

        class FakePage:
            url = "https://example.com/post"

            def __init__(self):
                self._original_url = "https://example.com/post"

            def wait_for_timeout(self, _ms):
                return None

            def locator(self, selector):
                if selector == "body":
                    return FakeBody()
                raise AssertionError(f"unexpected selector: {selector}")

        ok, msg = _verify_post_success(FakePage(), "hello world")
        self.assertTrue(ok)
        self.assertIn("提交后页面中已看不到原评论输入框", msg)
        mock_side_effect.assert_called_once()

    @patch("form_automation_local._detect_submission_side_effect", return_value="")
    def test_verify_post_success_accepts_reset_error_as_probable_success(self, mock_side_effect):
        class ExplodingBody:
            def all_inner_texts(self):
                raise RuntimeError("Target page, context or browser has been closed")

        class FakePage:
            url = "https://example.com/post"

            def __init__(self):
                self._original_url = "https://example.com/post"

            def wait_for_timeout(self, _ms):
                return None

            def locator(self, selector):
                if selector == "body":
                    return ExplodingBody()
                raise AssertionError(f"unexpected selector: {selector}")

        ok, msg = _verify_post_success(FakePage(), "hello world")
        self.assertTrue(ok)
        self.assertIn("Blogger/审核提交流程", msg)
        mock_side_effect.assert_called_once()

    @patch("form_automation_local._find_target_presence_in_comments", return_value="评论区链接中出现目标标识 'bearclicker.net'")
    def test_verify_post_success_accepts_target_presence_in_comments(self, mock_target_presence):
        class FakeBody:
            def all_inner_texts(self):
                return [""]

        class FakePage:
            url = "https://example.com/post"

            def __init__(self):
                self._original_url = "https://example.com/post"

            def wait_for_timeout(self, _ms):
                return None

            def locator(self, selector):
                if selector == "body":
                    return FakeBody()
                raise AssertionError(f"unexpected selector: {selector}")

        ok, msg = _verify_post_success(
            FakePage(),
            "hello world",
            target_url="https://bearclicker.net/",
            anchor_text="Bear Clicker",
        )
        self.assertTrue(ok)
        self.assertIn("评论区链接中出现目标标识", msg)
        mock_target_presence.assert_called_once()

    @patch("form_automation_local._LINK_FORMAT_DETECTOR")
    def test_resolve_runtime_link_format_defaults_blogspot_to_html(self, mock_detector):
        mock_detector.analyze_website.return_value = {
            "recommended_format": "unknown",
            "evidence_type": "unknown",
            "confidence": 0.0,
        }
        fmt, analysis = resolve_runtime_link_format("https://example.blogspot.com/post", "")
        self.assertEqual(fmt, "html")
        self.assertEqual(analysis["evidence_type"], "url_blogger_hint")

    def test_resolve_runtime_link_format_prefers_source_master_final_format(self):
        fmt, analysis = resolve_runtime_link_format(
            "https://example.com/post",
            "",
            source_row={
                "最终链接格式": "html",
                "格式检测证据": "vision_format_capability",
                "格式检测置信度": "0.91",
                "格式检测阶段": "vision_probe",
            },
        )
        self.assertEqual(fmt, "html")
        self.assertEqual(analysis["evidence_type"], "vision_format_capability")
        self.assertEqual(analysis["stage"], "vision_probe")

    def test_default_contact_email_constant_is_available(self):
        self.assertEqual(DEFAULT_CONTACT_EMAIL, "slideology0816@gmail.com")

    def test_browser_policy_defaults_to_9666_and_no_front(self):
        browser_cfg = merge_browser_config({})
        self.assertEqual(browser_cfg["connect_cdp_url"], "http://127.0.0.1:9666")
        self.assertEqual(browser_cfg["allow_only_cdp_url"], "http://127.0.0.1:9666")
        self.assertFalse(browser_cfg["bring_to_front"])
        self.assertTrue(browser_cfg["require_cdp"])

    def test_browser_policy_rejects_non_whitelisted_cdp_url(self):
        browser_cfg = merge_browser_config({"allow_only_cdp_url": "http://127.0.0.1:9666"})
        with self.assertRaises(RuntimeError):
            ensure_allowed_cdp_url("http://127.0.0.1:9222", browser_cfg)


if __name__ == "__main__":
    unittest.main()
