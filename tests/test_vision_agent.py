import unittest

from vision_agent import _extract_json, _format_failure_message


class VisionAgentTests(unittest.TestCase):
    def test_extract_json_from_code_block(self):
        raw = """```json
        {"textarea_x": 100, "textarea_y": 200}
        ```"""
        parsed, error = _extract_json(raw)
        self.assertIsNone(error)
        self.assertEqual(parsed["textarea_x"], 100)

    def test_extract_json_invalid_payload(self):
        parsed, error = _extract_json("not-json")
        self.assertIsNone(parsed)
        self.assertEqual(error, "vision_invalid_json")

    def test_format_failure_message(self):
        self.assertIn("提交按钮", _format_failure_message("submit_not_found"))


if __name__ == "__main__":
    unittest.main()
