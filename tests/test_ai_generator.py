import unittest

from ai_generator import get_anchor_for_format


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


if __name__ == "__main__":
    unittest.main()
