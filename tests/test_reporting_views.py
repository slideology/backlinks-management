import unittest

from legacy_feishu_history import LegacyFeishuHistoryStore, parse_legacy_tab_rows
from reporting_views import (
    build_legacy_history_rows,
    build_legacy_source_library_rows,
    build_posting_record_rows,
    build_source_summary_rows,
    build_target_site_rows,
)


class ReportingViewsTests(unittest.TestCase):
    def setUp(self):
        self.headers = [
            "ID",
            "Type",
            "URL",
            "Discovered_From",
            "Has_Captcha",
            "Link_Strategy",
            "Link_Format",
            "Has_URL_Field",
            "Status",
            "Priority",
            "Target_Website",
            "Keywords",
            "Anchor_Text",
            "Comment_Content",
            "Comment_Content_ZH",
            "Execution_Date",
            "Success_URL",
            "Notes",
            "Last_Updated",
            "Daily_Batch",
        ]
        self.rows = [
            self.headers,
            [
                "1",
                "blog_comment",
                "https://example.com/post",
                "ahrefs:bearclicker.net",
                "No",
                "comment_body",
                "html",
                "Yes",
                "completed",
                "high",
                "https://bearclicker.net/",
                "",
                "",
                "Great post about air fryer apples.",
                "这是一条中文翻译。",
                "2026-03-18",
                "https://example.com/post",
                "",
                "2026-03-18 10:00:00",
                "Batch-20260318",
            ],
            [
                "2",
                "blog_comment",
                "https://another.com/post",
                "ahrefs:nanobananaimage.com",
                "Yes",
                "comment_body",
                "markdown",
                "No",
                "failed",
                "medium",
                "https://nanobananaimage.com/",
                "",
                "",
                "Thanks for sharing.",
                "谢谢分享。",
                "2026-03-18",
                "",
                "textarea_not_found",
                "2026-03-18 11:00:00",
                "Batch-20260318",
            ],
        ]
        legacy_rows = [
            ["Page ascore", "Source title", "Source url", None, None, "游戏站"],
            [37, "Example Title", [{"link": "https://example.com/post", "text": "https://example.com/post"}], "n", "b", 1],
        ]
        history_records, source_rows = parse_legacy_tab_rows(legacy_rows, "wordle2.io", "sheet123")
        self.history_store = LegacyFeishuHistoryStore(history_records, source_rows=source_rows)

    def test_build_source_summary_rows_keeps_comment_content_raw(self):
        result = build_source_summary_rows(self.rows, self.headers, self.history_store)
        by_url = {row[1]: row for row in result}
        failed_row = by_url["https://another.com/post"]
        self.assertEqual(failed_row[8], "失败")
        self.assertEqual(failed_row[11], "谢谢分享。")

        completed_row = by_url["https://example.com/post"]
        self.assertEqual(completed_row[0], "Example Title")
        self.assertEqual(completed_row[9], "https://bearclicker.net/")
        self.assertEqual(completed_row[10], "Great post about air fryer apples.")
        self.assertIn("bearclicker.net", completed_row[17])

    def test_build_posting_record_rows_localizes_metadata_only(self):
        result = build_posting_record_rows(self.rows, self.headers, self.history_store)
        completed = next(row for row in result if row[1] == "https://example.com/post")
        self.assertEqual(completed[4], "已完成")
        self.assertEqual(completed[5], "Great post about air fryer apples.")
        self.assertEqual(completed[10], "HTML")

    def test_build_target_site_rows_merges_targets_and_legacy_mappings(self):
        targets = [
            {
                "url": "https://bearclicker.net/",
                "anchor_text": "bear clicker",
                "description": "A fun game",
                "active": True,
            }
        ]
        rows = build_target_site_rows(targets, self.history_store.promoted_site_map)
        self.assertEqual(rows[0][0], "bearclicker.net")
        self.assertEqual(rows[0][4], "是")
        self.assertTrue(any(row[0] == "nanobananaimage.org" for row in rows))

    def test_build_legacy_history_rows_are_chinese_friendly(self):
        rows = build_legacy_history_rows(self.history_store)
        self.assertEqual(rows[0][3], "bearclicker.net")
        self.assertEqual(rows[0][4], "是")

    def test_build_legacy_source_library_rows_include_raw_markers(self):
        rows = build_legacy_source_library_rows(self.history_store)
        self.assertEqual(rows[0][4], "wordle2.io")
        self.assertEqual(rows[0][6], "n")
        self.assertEqual(rows[0][7], "b")

    def test_build_legacy_source_library_rows_dedupes_by_root_domain(self):
        duplicate_rows = [
            ["Page ascore", "Source title", "Source url", None, None, "游戏站"],
            [12, "Lower Score", [{"link": "https://example.com/post-a", "text": "https://example.com/post-a"}], "", "", 1],
            [20, "Higher Score", [{"link": "https://example.com/post-b", "text": "https://example.com/post-b"}], "", "", 1],
            [18, "Other Domain", [{"link": "https://another.com/post", "text": "https://another.com/post"}], "", "", 1],
        ]
        history_records, source_rows = parse_legacy_tab_rows(duplicate_rows, "dup-tab", "sheet456")
        store = LegacyFeishuHistoryStore(history_records, source_rows=source_rows)

        rows = build_legacy_source_library_rows(store)

        self.assertEqual(len(rows), 2)
        by_domain = {row[2]: row for row in rows}
        self.assertEqual(by_domain["example.com"][1], "https://example.com/post-b")

    def test_build_legacy_source_library_rows_sorts_by_score_then_marker(self):
        rows = [
            ["Page ascore", "Source title", "Source url", None, None, "游戏站"],
            [18, "No Marker", [{"link": "https://c.com/post", "text": "https://c.com/post"}], "", "", 1],
            [18, "With Marker", [{"link": "https://b.com/post", "text": "https://b.com/post"}], "n", "", 1],
            [25, "Higher Score", [{"link": "https://a.com/post", "text": "https://a.com/post"}], "", "", 1],
        ]
        history_records, source_rows = parse_legacy_tab_rows(rows, "sort-tab", "sheet789")
        store = LegacyFeishuHistoryStore(history_records, source_rows=source_rows)

        result = build_legacy_source_library_rows(store)

        self.assertEqual(result[0][1], "https://a.com/post")
        self.assertEqual(result[1][1], "https://b.com/post")
        self.assertEqual(result[2][1], "https://c.com/post")


if __name__ == "__main__":
    unittest.main()
