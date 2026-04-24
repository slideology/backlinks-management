import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from source_retry_audit import build_repeated_unsuccessful_candidates, write_candidate_reports


class SourceRetryAuditTests(unittest.TestCase):
    def test_build_candidates_only_includes_unsuccessful_rows_over_threshold(self):
        workbook = Mock()
        workbook.read_sheet_dicts.return_value = (
            [],
            [
                {
                    "来源链接": "https://example.com/post-a",
                    "目标站标识": "bearclicker.net",
                    "状态": "待重试",
                    "最近成功时间": "",
                    "最近失败分类": "vision_unavailable",
                    "最近失败原因": "Vision timeout",
                },
                {
                    "来源链接": "https://example.com/post-b",
                    "目标站标识": "bearclicker.net",
                    "状态": "成功",
                    "最近成功时间": "2026-04-01 10:00:00",
                    "最近失败分类": "",
                    "最近失败原因": "",
                },
            ],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "launchd.stdout.log"
            log_path.write_text(
                "\n".join(
                    [
                        "🚀 开始处理来源 https://example.com/post-a -> 站点 bearclicker.net",
                        "🚀 开始处理来源 https://example.com/post-a -> 站点 bearclicker.net",
                        "🚀 开始处理来源 https://example.com/post-a -> 站点 bearclicker.net",
                        "🚀 开始处理来源 https://example.com/post-b -> 站点 bearclicker.net",
                        "🚀 开始处理来源 https://example.com/post-b -> 站点 bearclicker.net",
                        "🚀 开始处理来源 https://example.com/post-b -> 站点 bearclicker.net",
                    ]
                ),
                encoding="utf-8",
            )

            candidates = build_repeated_unsuccessful_candidates(
                workbook,
                threshold=3,
                log_path=str(log_path),
            )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["来源链接"], "https://example.com/post-a")
        self.assertEqual(candidates[0]["尝试次数"], 3)

    def test_write_candidate_reports_outputs_json_and_csv(self):
        candidates = [
            {
                "来源链接": "https://example.com/post-a",
                "目标站点": "bearclicker.net",
                "尝试次数": 7,
                "当前状态": "待重试",
                "最近失败分类": "vision_unavailable",
                "最近失败原因": "Vision timeout",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            json_path, csv_path = write_candidate_reports(candidates, threshold=5, output_dir=tmpdir)
            self.assertTrue(json_path.exists())
            self.assertTrue(csv_path.exists())
            self.assertIn("https://example.com/post-a", json_path.read_text(encoding="utf-8"))
            self.assertIn("bearclicker.net", csv_path.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
