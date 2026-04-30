import unittest

from source_probe_manual_review import build_manual_review_rows, classify_worth_no_row


class SourceProbeManualReviewTests(unittest.TestCase):
    def test_classify_comment_missing_as_direct_clean(self):
        result = classify_worth_no_row({"页面探测失败原因": "DOM 与页面文本均未发现评论区线索"})
        self.assertEqual(result["建议动作"], "直接清理")
        self.assertEqual(result["原因大类"], "无评论区/评论关闭")

    def test_classify_login_wall_as_login_freeze(self):
        result = classify_worth_no_row({"页面探测失败原因": "🔒 该站设置了登录墙，必须有账号才能评论。"})
        self.assertEqual(result["建议动作"], "登录冻结")
        self.assertEqual(result["原因大类"], "必须登录后才能评论")

    def test_classify_challenge_as_gray_pool(self):
        result = classify_worth_no_row({"页面探测失败原因": "页面存在验证码或 Cloudflare 挑战"})
        self.assertEqual(result["建议动作"], "验证码灰区")
        self.assertEqual(result["原因大类"], "验证码/Cloudflare 挑战")

    def test_build_manual_review_rows_filters_non_no_rows(self):
        rows = [
            {"来源链接": "https://a.example", "是否值得发帖": "否", "页面探测失败原因": "未发现评论区"},
            {"来源链接": "https://b.example", "是否值得发帖": "是", "页面探测失败原因": ""},
        ]
        reviewed = build_manual_review_rows(rows)
        self.assertEqual(len(reviewed), 1)
        self.assertEqual(reviewed[0]["来源链接"], "https://a.example")


if __name__ == "__main__":
    unittest.main()
