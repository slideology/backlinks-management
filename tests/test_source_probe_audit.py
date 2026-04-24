import unittest

from source_probe_audit import build_probe_timeout_bundle, classify_probe_outcome


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

    def test_build_probe_timeout_bundle_marks_timeout_for_review(self):
        audit_result, capability, preprobe_meta = build_probe_timeout_bundle("页面探测超时（>120秒）")

        self.assertEqual(audit_result["status"], "failed")
        self.assertEqual(capability["status"], "failed")
        self.assertFalse(preprobe_meta["ok"])
        self.assertEqual(preprobe_meta["diagnostic_category"], "preprobe_timeout")
        self.assertIn("超时", preprobe_meta["diagnosis"])


if __name__ == "__main__":
    unittest.main()
