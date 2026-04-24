import unittest
from unittest.mock import patch

from webhook_sender import WebhookSender


class WebhookSenderTests(unittest.TestCase):
    def test_send_exception_alert_builds_red_card(self):
        sender = WebhookSender("https://example.com/hook")

        with patch.object(sender, "_send_payload", return_value=True) as mock_send:
            ok = sender.send_exception_alert(
                "🚨 外链任务异常中断",
                "任务超过 300 秒没有新的日志进展，已被 watchdog 自动终止。",
                {"CDP 端口": "9666", "浏览器": "Google Chrome Canary"},
            )

        self.assertTrue(ok)
        payload = mock_send.call_args[0][0]
        self.assertEqual(payload["msg_type"], "interactive")
        self.assertEqual(payload["card"]["header"]["template"], "red")
        markdown = payload["card"]["elements"][0]["content"]
        self.assertIn("任务超过 300 秒没有新的日志进展", markdown)
        self.assertIn("CDP 端口", markdown)
        self.assertIn("9666", markdown)


if __name__ == "__main__":
    unittest.main()
