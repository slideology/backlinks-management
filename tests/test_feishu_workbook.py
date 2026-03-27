import tempfile
import unittest
from unittest.mock import Mock

from feishu_workbook import FeishuWorkbook


class FeishuWorkbookTests(unittest.TestCase):
    def test_normalize_key_field_matches_rich_text_and_plain_url(self):
        rich_text = [
            {
                "cellPosition": None,
                "link": "https://Example.com/post?utm_source=test",
                "text": "https://Example.com/post?utm_source=test",
                "type": "url",
            }
        ]

        normalized_rich = FeishuWorkbook._normalize_key_field("来源链接", rich_text)
        normalized_plain = FeishuWorkbook._normalize_key_field("来源链接", "https://example.com/post")

        self.assertEqual(normalized_rich, "https://example.com/post")
        self.assertEqual(normalized_plain, "https://example.com/post")
        self.assertEqual(normalized_rich, normalized_plain)

    def test_upsert_failure_is_buffered_locally(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = Mock()
            client.spreadsheet_token = "sheet"
            client.read_range.side_effect = RuntimeError("dns failure")
            workbook = FeishuWorkbook(
                client=client,
                config={"write_buffer_file": f"{tmpdir}/buffer.json"},
                spreadsheet_token="sheet",
                sheet_ids={"records": "sheet_1"},
                spreadsheet_url="",
            )

            row_index = workbook.upsert_sheet_dict(
                "records",
                ["来源链接", "目标站标识", "状态"],
                ["来源链接", "目标站标识"],
                {"来源链接": "https://example.com/post", "目标站标识": "bearclicker.net", "状态": "待重试"},
            )

            self.assertEqual(row_index, 0)
            buffered = workbook._load_buffer()
            self.assertEqual(len(buffered), 1)
            self.assertEqual(buffered[0]["op"], "upsert")
            self.assertEqual(buffered[0]["key"], "records")

    def test_flush_buffered_upsert_replays_when_client_recovers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = Mock()
            client.spreadsheet_token = "sheet"
            client.read_range.return_value = [["来源链接", "目标站标识", "状态"]]
            workbook = FeishuWorkbook(
                client=client,
                config={"write_buffer_file": f"{tmpdir}/buffer.json"},
                spreadsheet_token="sheet",
                sheet_ids={"records": "sheet_1"},
                spreadsheet_url="",
            )
            workbook._enqueue_write(
                {
                    "op": "upsert",
                    "key": "records",
                    "headers": ["来源链接", "目标站标识", "状态"],
                    "key_fields": ["来源链接", "目标站标识"],
                    "row": {"来源链接": "https://example.com/post", "目标站标识": "bearclicker.net", "状态": "待重试"},
                    "max_rows": 50000,
                }
            )

            flushed = workbook.flush_buffered_writes(limit=10)

            self.assertEqual(flushed, 1)
            self.assertEqual(workbook._load_buffer(), [])
            client.write_range.assert_called_once_with(
                "sheet_1!A2:C2",
                [["https://example.com/post", "bearclicker.net", "待重试"]],
            )


if __name__ == "__main__":
    unittest.main()
