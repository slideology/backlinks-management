import unittest
from unittest.mock import Mock, patch

from feishu_integration import BACKLINK_HEADERS_ZH, DEFAULT_HEADERS, FeishuClient, build_execution_record


class FeishuIntegrationTests(unittest.TestCase):
    @patch("feishu_integration.requests.post")
    def test_get_tenant_access_token_success(self, mock_post):
        mock_post.return_value = Mock(
            status_code=200,
            raise_for_status=Mock(),
            json=Mock(return_value={"code": 0, "tenant_access_token": "token-123"}),
        )

        client = FeishuClient("app", "secret", "sheet", "sheet_id")
        self.assertEqual(client.get_tenant_access_token(), "token-123")

    def test_build_execution_record_uses_expected_headers(self):
        record = build_execution_record(
            {
                "google_sheets_row": 9,
                "url": "https://example.com/post",
                "success": False,
                "reason": "textarea_not_found",
                "format": "markdown",
                "target_website": "https://slideology.com",
                "batch_token": "Batch-20260313",
                "used_vision": True,
                "diagnostic_category": "textarea_not_found",
            }
        )

        for header in DEFAULT_HEADERS:
            self.assertIn(header, record)
        self.assertEqual(record["Google Sheets Row"], 9)
        self.assertEqual(record["Status"], "failed")
        self.assertEqual(record["Used Vision"], "yes")

    def test_upsert_backlink_row_writes_target_range(self):
        client = FeishuClient("app", "secret", "sheet", "sheet_id")
        client.ensure_backlink_headers = Mock(return_value=BACKLINK_HEADERS_ZH)
        client.write_range = Mock()

        row = ["值"] * len(BACKLINK_HEADERS_ZH)
        row_index = client.upsert_backlink_row(7, row)

        self.assertEqual(row_index, 7)
        client.write_range.assert_called_once_with("sheet_id!A7:T7", [row])


if __name__ == "__main__":
    unittest.main()
