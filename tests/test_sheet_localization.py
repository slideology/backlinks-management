import unittest
from unittest.mock import patch

from sheet_localization import (
    FEISHU_HEADERS_ZH,
    localize_basic_value,
    localize_updates_for_storage,
    normalize_google_value,
    translate_row_for_storage,
)


class SheetLocalizationTests(unittest.TestCase):
    def test_normalize_google_value_supports_chinese_status(self):
        self.assertEqual(normalize_google_value("Status", "已完成"), "completed")
        self.assertEqual(normalize_google_value("Priority", "中"), "medium")
        self.assertEqual(normalize_google_value("Daily_Batch", "批次-20260313"), "Batch-20260313")

    def test_localize_basic_value_translates_enums(self):
        self.assertEqual(localize_basic_value("Status", "failed"), "失败")
        self.assertEqual(localize_basic_value("Link_Strategy", "url_field"), "仅网址字段")
        self.assertEqual(localize_basic_value("Daily_Batch", "Batch-20260313"), "批次-20260313")

    @patch("sheet_localization.translate_fields_to_chinese")
    def test_translate_row_for_storage_preserves_links(self, mock_translate):
        mock_translate.return_value = {
            "Keywords": "演示设计，视觉叙事",
            "Anchor_Text": 'HTML: 访问 <a href="https://slideology.com">专业幻灯片设计</a>',
            "Comment_Content": "谢谢分享，内容很有帮助！",
            "Notes": "机器人自动提交成功",
        }
        row = {
            "ID": "1",
            "Type": "profile",
            "URL": "https://example.com/post",
            "Discovered_From": "ahrefs:sprunki-game.io",
            "Has_Captcha": "No",
            "Link_Strategy": "url_field",
            "Link_Format": "markdown",
            "Has_URL_Field": "No",
            "Status": "completed",
            "Priority": "medium",
            "Target_Website": "https://slideology.com",
            "Keywords": "presentation design, visual storytelling",
            "Anchor_Text": 'HTML: visit <a href="https://slideology.com">presentation design</a>',
            "Comment_Content": "Thanks for sharing!",
            "Execution_Date": "2026-03-13",
            "Success_URL": "https://example.com/post",
            "Notes": "automation success",
            "Last_Updated": "2026-03-13 10:00:00",
            "Daily_Batch": "Batch-20260313",
        }

        localized = translate_row_for_storage(row)

        self.assertEqual(localized["Type"], "资料页")
        self.assertEqual(localized["Status"], "已完成")
        self.assertEqual(localized["URL"], "https://example.com/post")
        self.assertEqual(localized["Target_Website"], "https://slideology.com")
        self.assertEqual(localized["Daily_Batch"], "批次-20260313")
        self.assertEqual(len(FEISHU_HEADERS_ZH), 19)

    @patch("sheet_localization.translate_fields_to_chinese")
    def test_localize_updates_for_storage_translates_free_text(self, mock_translate):
        mock_translate.return_value = {"Notes": "点击评论框后未能稳定输入"}

        localized = localize_updates_for_storage(
            {"Status": "failed", "Notes": "click no effect", "Daily_Batch": "Batch-20260313"}
        )

        self.assertEqual(localized["Status"], "失败")
        self.assertEqual(localized["Notes"], "点击评论框后未能稳定输入")
        self.assertEqual(localized["Daily_Batch"], "批次-20260313")


if __name__ == "__main__":
    unittest.main()
