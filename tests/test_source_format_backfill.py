import unittest

from source_format_backfill import build_source_probe_update, summarize_results


class SourceFormatBackfillTests(unittest.TestCase):
    def test_build_source_probe_update_maps_probe_fields(self):
        row = {
            "来源链接": "https://example.com/post",
            "来源标题": "Example",
        }
        capability = {
            "stage": "vision_probe",
            "status": "completed",
            "vision_used": True,
            "static_result": {
                "recommended_format": "unknown",
            },
            "final_result": {
                "recommended_format": "html",
                "evidence_type": "vision_format_capability",
                "confidence": 0.91,
            },
        }

        updated = build_source_probe_update(row, capability, "2026-03-23 15:00:00")

        self.assertEqual(updated["初始链接格式"], "unknown")
        self.assertEqual(updated["最终链接格式"], "html")
        self.assertEqual(updated["格式检测阶段"], "vision_probe")
        self.assertEqual(updated["格式检测证据"], "vision_format_capability")
        self.assertEqual(updated["格式检测置信度"], "0.91")
        self.assertEqual(updated["是否视觉复核"], "是")
        self.assertEqual(updated["格式检测状态"], "completed")

    def test_summarize_results_counts_upgrades_and_conflicts(self):
        summary = summarize_results(
            [
                {
                    "status": "completed",
                    "static_result": {"recommended_format": "unknown"},
                    "final_result": {"recommended_format": "html"},
                },
                {
                    "status": "conflict",
                    "static_result": {"recommended_format": "plain_text_autolink"},
                    "final_result": {"recommended_format": "html"},
                },
                {
                    "status": "failed",
                    "static_result": {"recommended_format": "unknown"},
                    "final_result": {"recommended_format": "unknown"},
                },
            ]
        )

        self.assertEqual(summary["total_scanned"], 3)
        self.assertEqual(summary["final_format_counts"]["html"], 2)
        self.assertEqual(summary["upgraded_unknown_to_html"], 1)
        self.assertEqual(summary["upgraded_plain_text_autolink_to_html"], 1)
        self.assertEqual(summary["conflict_count"], 1)
        self.assertEqual(summary["failed_count"], 1)


if __name__ == "__main__":
    unittest.main()
