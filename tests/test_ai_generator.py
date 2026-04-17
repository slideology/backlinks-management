import unittest
from unittest.mock import patch

from ai_generator import (
    _is_retryable_ai_error,
    generate_localized_bundle_for_target,
    get_anchor_for_format,
    summarize_comment_discussion,
    translate_content_fields,
)


class AIGeneratorTests(unittest.TestCase):
    def test_plain_text_autolink_returns_raw_url(self):
        target_url = "https://bearclicker.net/"
        self.assertEqual(
            get_anchor_for_format("bear clicker", "plain_text_autolink", target_url),
            target_url,
        )

    def test_unknown_falls_back_to_raw_url(self):
        target_url = "https://bearclicker.net/"
        self.assertEqual(
            get_anchor_for_format("bear clicker", "unknown", target_url),
            target_url,
        )

    def test_retryable_ai_error_detects_503(self):
        self.assertTrue(_is_retryable_ai_error(RuntimeError("503 UNAVAILABLE")))
        self.assertFalse(_is_retryable_ai_error(RuntimeError("invalid api key")))

    @patch("ai_generator._generate_content", side_effect=RuntimeError("503 UNAVAILABLE"))
    def test_translate_content_fields_falls_back_to_original_values(self, _mock_generate):
        payload = translate_content_fields({"comment_content_zh": "hello world"}, "Simplified Chinese")
        self.assertEqual(payload["comment_content_zh"], "hello world")

    @patch("ai_generator._generate_content", side_effect=RuntimeError("503 UNAVAILABLE"))
    def test_summarize_comment_discussion_returns_empty_on_api_failure(self, _mock_generate):
        self.assertEqual(summarize_comment_discussion(["first", "second"], "English"), "")

    @patch("ai_generator.translate_comment_to_chinese", return_value="中文兜底")
    @patch("ai_generator.generate_comment_for_target", return_value="Fallback comment https://bearclicker.net/")
    @patch("ai_generator._generate_content", side_effect=RuntimeError("503 UNAVAILABLE"))
    def test_generate_localized_bundle_survives_retryable_api_failure(
        self,
        _mock_generate,
        _mock_generate_comment,
        mock_translate,
    ):
        bundle = generate_localized_bundle_for_target(
            {
                "url": "https://bearclicker.net/",
                "description": "desc",
                "anchor_text": "bear clicker",
            },
            "plain_text",
            {
                "language_name": "English",
                "language_code": "en",
                "title": "Example title",
                "description": "Example description",
                "excerpt": "Excerpt",
                "comments_summary": "",
                "url": "https://example.com/post",
            },
        )
        self.assertEqual(bundle["anchor_text"], "https://bearclicker.net/")
        self.assertIn("https://bearclicker.net/", bundle["comment_content"])
        self.assertEqual(bundle["comment_content_zh"], "中文兜底")
        mock_translate.assert_called_once()

    @patch("ai_generator.translate_comment_to_chinese", return_value="不应该调用")
    @patch("ai_generator.generate_comment_for_target", return_value="Fallback comment https://bearclicker.net/")
    @patch("ai_generator._generate_content", side_effect=RuntimeError("503 UNAVAILABLE"))
    def test_generate_localized_bundle_can_skip_chinese_translation(
        self,
        _mock_generate,
        _mock_generate_comment,
        mock_translate,
    ):
        bundle = generate_localized_bundle_for_target(
            {
                "url": "https://bearclicker.net/",
                "description": "desc",
                "anchor_text": "bear clicker",
            },
            "plain_text",
            {
                "language_name": "English",
                "language_code": "en",
                "title": "Example title",
                "description": "Example description",
                "excerpt": "Excerpt",
                "comments_summary": "",
                "url": "https://example.com/post",
            },
            include_chinese_translation=False,
        )
        self.assertEqual(bundle["comment_content_zh"], "")
        mock_translate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
