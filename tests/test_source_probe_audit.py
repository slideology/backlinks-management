import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from source_probe_audit import (
    build_probe_timeout_bundle,
    classify_probe_outcome,
    ensure_probe_browser_ready,
    load_focus_urls,
)


class SourceProbeAuditTests(unittest.TestCase):
    def test_classify_probe_outcome_marks_excluded_urls_as_skipped(self):
        result = classify_probe_outcome(
            "https://spellbee.org/",
            {"status": "completed", "requires_login": "否", "has_comment_area": "是"},
            {"status": "completed", "final_result": {"recommended_format": "html"}},
            {"ok": True},
            set(),
            {"https://spellbee.org/"},
        )

        self.assertEqual(result["页面探测状态"], "skipped")
        self.assertEqual(result["是否值得发帖"], "否")

    def test_classify_probe_outcome_marks_login_pages_as_not_worth_posting(self):
        result = classify_probe_outcome(
            "https://example.com/post",
            {
                "status": "completed",
                "requires_login": "是",
                "login_evidence": "page_text_login_wall",
                "has_comment_area": "是",
            },
            {"status": "completed", "final_result": {"recommended_format": "html"}},
            {"ok": True},
            set(),
            set(),
        )

        self.assertEqual(result["页面探测状态"], "completed")
        self.assertEqual(result["是否值得发帖"], "否")
        self.assertIn("login", result["页面探测失败原因"])

    def test_classify_probe_outcome_marks_comment_missing_as_not_worth_posting(self):
        result = classify_probe_outcome(
            "https://example.com/post",
            {"status": "completed", "requires_login": "否", "has_comment_area": "否", "comment_evidence": "未发现评论区"},
            {"status": "completed", "final_result": {"recommended_format": "html"}},
            {"ok": True},
            set(),
            set(),
        )

        self.assertEqual(result["页面探测状态"], "completed")
        self.assertEqual(result["是否值得发帖"], "否")

    def test_classify_probe_outcome_marks_conflicts_for_review(self):
        result = classify_probe_outcome(
            "https://example.com/post",
            {"status": "completed", "requires_login": "否", "has_comment_area": "是"},
            {"status": "conflict", "final_result": {"recommended_format": "html"}},
            {"ok": True},
            set(),
            set(),
        )

        self.assertEqual(result["页面探测状态"], "completed")
        self.assertEqual(result["是否值得发帖"], "待确认")

    def test_classify_probe_outcome_marks_challenge_as_not_worth_posting(self):
        result = classify_probe_outcome(
            "https://example.com/post",
            {"status": "completed", "requires_login": "否", "has_comment_area": "是"},
            {"status": "skipped", "final_result": {"recommended_format": "unknown"}},
            {
                "ok": False,
                "diagnosis": "页面存在验证码或 Cloudflare 挑战",
                "diagnostic_category": "challenge",
                "challenge_after_expand": True,
            },
            set(),
            set(),
        )

        self.assertEqual(result["页面探测状态"], "completed")
        self.assertEqual(result["是否值得发帖"], "否")
        self.assertIn("Cloudflare", result["页面探测失败原因"])

    def test_classify_probe_outcome_prefers_challenge_over_login_when_dynamic_probe_hits_challenge(self):
        result = classify_probe_outcome(
            "https://example.com/post",
            {
                "status": "completed",
                "requires_login": "是",
                "login_evidence": "page_text_login_wall",
                "has_comment_area": "是",
            },
            {"status": "skipped", "final_result": {"recommended_format": "unknown"}},
            {
                "ok": False,
                "diagnosis": "页面存在验证码或 Cloudflare 挑战",
                "diagnostic_category": "challenge",
                "challenge_after_expand": True,
            },
            set(),
            set(),
        )

        self.assertEqual(result["页面探测状态"], "completed")
        self.assertEqual(result["是否值得发帖"], "否")
        self.assertIn("Cloudflare", result["页面探测失败原因"])

    def test_classify_probe_outcome_promotes_conflict_when_strong_positive_signals_exist(self):
        result = classify_probe_outcome(
            "https://example.com/post",
            {"status": "completed", "requires_login": "否", "has_comment_area": "是"},
            {"status": "conflict", "final_result": {"recommended_format": "html"}},
            {
                "ok": True,
                "comment_signal_found": True,
                "comment_entry_found": True,
                "comment_entry_expanded": True,
                "comment_form_visible": False,
            },
            set(),
            set(),
        )

        self.assertEqual(result["页面探测状态"], "completed")
        self.assertEqual(result["是否值得发帖"], "是")

    def test_classify_probe_outcome_promotes_runtime_unknown_when_strong_positive_signals_exist(self):
        result = classify_probe_outcome(
            "https://example.com/post",
            {"status": "completed", "requires_login": "否", "has_comment_area": "是"},
            {"status": "failed", "final_result": {"recommended_format": "unknown", "evidence_type": "runtime_unknown"}},
            {
                "ok": True,
                "comment_signal_found": True,
                "comment_form_visible": True,
            },
            set(),
            set(),
        )

        self.assertEqual(result["页面探测状态"], "completed")
        self.assertEqual(result["是否值得发帖"], "是")

    def test_classify_probe_outcome_promotes_probe_timeout_when_current_signals_are_strong(self):
        result = classify_probe_outcome(
            "https://example.com/post",
            {"status": "completed", "requires_login": "否", "has_comment_area": "是"},
            {"status": "failed", "final_result": {"recommended_format": "unknown", "evidence_type": "probe_timeout"}},
            {
                "ok": True,
                "comment_signal_found": True,
                "comment_entry_expanded": True,
                "comment_form_visible": True,
            },
            set(),
            set(),
        )

        self.assertEqual(result["页面探测状态"], "completed")
        self.assertEqual(result["是否值得发帖"], "是")

    def test_classify_probe_outcome_promotes_probe_failed_when_current_signals_are_strong(self):
        result = classify_probe_outcome(
            "https://example.com/post",
            {"status": "completed", "requires_login": "否", "has_comment_area": "是"},
            {"status": "failed", "final_result": {"recommended_format": "unknown", "evidence_type": "probe_failed"}},
            {
                "ok": True,
                "comment_signal_found": True,
                "comment_entry_found": True,
                "comment_form_visible": True,
            },
            set(),
            set(),
        )

        self.assertEqual(result["页面探测状态"], "completed")
        self.assertEqual(result["是否值得发帖"], "是")

    def test_build_probe_timeout_bundle_marks_timeout_for_review(self):
        audit_result, capability, preprobe_meta = build_probe_timeout_bundle("页面探测超时（>120秒）")

        self.assertEqual(audit_result["status"], "failed")
        self.assertEqual(capability["status"], "failed")
        self.assertFalse(preprobe_meta["ok"])
        self.assertEqual(preprobe_meta["diagnostic_category"], "preprobe_timeout")
        self.assertIn("超时", preprobe_meta["diagnosis"])

    def test_classify_probe_outcome_prefers_login_audit_over_missing_preprobe_signal(self):
        result = classify_probe_outcome(
            "http://answers.familyecho.com/4145/increase-the-font-size-before-print-too-small-when-printed-out",
            {
                "status": "completed",
                "requires_login": "是",
                "login_evidence": "page_text_login_wall",
                "has_comment_area": "是",
            },
            {"status": "completed", "final_result": {"recommended_format": "html"}},
            {
                "ok": False,
                "diagnosis": "DOM 与页面文本均未发现评论区线索",
                "diagnostic_category": "comment_signal_missing",
            },
            set(),
            set(),
        )

        self.assertEqual(result["页面探测状态"], "completed")
        self.assertEqual(result["是否值得发帖"], "否")
        self.assertEqual(result["页面探测失败原因"], "page_text_login_wall")

    def test_classify_probe_outcome_ignores_comment_missing_preprobe_when_audit_found_comment_area(self):
        result = classify_probe_outcome(
            "https://example.com/post",
            {
                "status": "completed",
                "requires_login": "否",
                "has_comment_area": "是",
            },
            {"status": "completed", "final_result": {"recommended_format": "html"}},
            {
                "ok": False,
                "diagnosis": "DOM 与页面文本均未发现评论区线索",
                "diagnostic_category": "comment_signal_missing",
            },
            set(),
            set(),
        )

        self.assertEqual(result["页面探测状态"], "completed")
        self.assertEqual(result["是否值得发帖"], "是")

    def test_classify_probe_outcome_inherits_existing_login_reason_on_timeout(self):
        result = classify_probe_outcome(
            "https://example.com/post",
            {"status": "failed", "requires_login": "", "has_comment_area": ""},
            {"status": "failed", "final_result": {"recommended_format": "unknown", "evidence_type": "probe_timeout"}},
            {
                "ok": False,
                "diagnosis": "页面探测子进程超时（>110s）",
                "diagnostic_category": "preprobe_timeout",
            },
            set(),
            set(),
            existing_probe={
                "是否需要登录": "是",
                "页面探测失败原因": "必须登录后才能评论",
                "评论区是否存在": "是",
                "是否值得发帖": "否",
            },
        )

        self.assertEqual(result["页面探测状态"], "completed")
        self.assertEqual(result["是否值得发帖"], "否")
        self.assertIn("登录", result["页面探测失败原因"])

    def test_classify_probe_outcome_promotes_existing_review_with_comment_signals_on_timeout(self):
        result = classify_probe_outcome(
            "https://example.com/post",
            {"status": "failed", "requires_login": "", "has_comment_area": ""},
            {"status": "failed", "final_result": {"recommended_format": "unknown", "evidence_type": "probe_timeout"}},
            {
                "ok": False,
                "diagnosis": "页面探测子进程超时（>110s）",
                "diagnostic_category": "preprobe_timeout",
            },
            set(),
            set(),
            existing_probe={
                "评论区是否存在": "是",
                "是否需要登录": "否",
                "是否值得发帖": "待确认",
                "页面探测失败原因": "格式检测存在冲突，建议复核",
                "comment_entry_found": True,
            },
        )

        self.assertEqual(result["页面探测状态"], "completed")
        self.assertEqual(result["是否值得发帖"], "是")

    def test_probe_browser_ready_rejects_9666_reuse(self):
        with self.assertRaises(RuntimeError):
            ensure_probe_browser_ready(
                {
                    "connect_cdp_url": "http://127.0.0.1:9666",
                    "browser": {"allow_only_cdp_url": "http://127.0.0.1:9666"},
                }
            )

    def test_load_focus_urls_supports_plain_text_lists(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "focus.txt"
            path.write_text(
                "https://example.com/post\n"
                " \n"
                "not a url\n",
                encoding="utf-8",
            )
            focus = load_focus_urls(str(path))

        self.assertEqual(focus, {"https://example.com/post"})


if __name__ == "__main__":
    unittest.main()
