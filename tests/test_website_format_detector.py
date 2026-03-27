import unittest
from unittest.mock import Mock, patch

from website_format_detector import WebsiteFormatDetector


class WebsiteFormatDetectorTests(unittest.TestCase):
    @patch("website_format_detector.requests.Session.get")
    def test_detects_markdown_from_editor_help(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            text="""
            <html>
              <head><title>Markdown Comments</title></head>
              <body>
                <form class="comment-form">
                  <textarea name="comment"></textarea>
                  <p>You can use Markdown formatting like [text](https://example.com).</p>
                </form>
              </body>
            </html>
            """,
            raise_for_status=Mock(),
        )

        result = WebsiteFormatDetector().analyze_website("https://example.com/markdown")

        self.assertEqual(result["recommended_format"], "markdown")
        self.assertEqual(result["evidence_type"], "editor_markdown")

    @patch("website_format_detector.requests.Session.get")
    def test_detects_plain_text_autolink_from_historical_comment_dom(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            text="""
            <html>
              <head><title>Autolink Comments</title></head>
              <body>
                <section id="comments">
                  <article class="comment">
                    <div class="comment-body">
                      Visit <a href="https://bearclicker.net/">https://bearclicker.net/</a> for more.
                    </div>
                  </article>
                </section>
              </body>
            </html>
            """,
            raise_for_status=Mock(),
        )

        result = WebsiteFormatDetector().analyze_website("https://example.com/autolink")

        self.assertEqual(result["recommended_format"], "plain_text_autolink")
        self.assertEqual(result["evidence_type"], "historical_autolink")

    @patch("website_format_detector.requests.Session.get")
    def test_detects_html_from_historical_anchor_text_link(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            text="""
            <html>
              <head><title>Anchor Text Comments</title></head>
              <body>
                <section id="comments">
                  <article class="comment">
                    <div class="comment-body">
                      And will restrict the benefits from this information.
                      <a href="https://example.com/herbe-de-pampa">herbe de pampa</a>
                    </div>
                  </article>
                </section>
              </body>
            </html>
            """,
            raise_for_status=Mock(),
        )

        result = WebsiteFormatDetector().analyze_website("https://example.com/anchor-text")

        self.assertEqual(result["recommended_format"], "html")
        self.assertEqual(result["evidence_type"], "historical_anchor_text_link")

    @patch("website_format_detector.requests.Session.get")
    def test_returns_unknown_without_comment_evidence(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            text="""
            <html>
              <head><title>No Comment Clues</title></head>
              <body>
                <nav><a href="https://example.com/home">Home</a></nav>
                <article><p>Regular article content only.</p></article>
              </body>
            </html>
            """,
            raise_for_status=Mock(),
        )

        result = WebsiteFormatDetector().analyze_website("https://example.com/unknown")

        self.assertEqual(result["recommended_format"], "unknown")
        self.assertEqual(result["evidence_type"], "unknown")

    @patch("website_format_detector.requests.Session.get")
    def test_detects_html_from_comment_form_website_and_history(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            text="""
            <html>
              <head><title>Comment Form Fallback</title></head>
              <body>
                <div id="comments" class="comments-area">
                  <ol class="comment-list">
                    <li id="comment-12" class="comment">
                      <article class="comment-body">
                        <div class="comment-entry">Great write-up and really useful tips.</div>
                      </article>
                    </li>
                  </ol>
                  <div id="respond" class="comment-respond">
                    <form id="commentform" class="comment-form">
                      <textarea id="comment" name="comment"></textarea>
                      <input id="author" name="author" />
                      <input id="email" name="email" />
                      <input id="url" name="url" />
                    </form>
                  </div>
                </div>
              </body>
            </html>
            """,
            raise_for_status=Mock(),
        )

        result = WebsiteFormatDetector().analyze_website("https://example.com/fallback-html")

        self.assertEqual(result["recommended_format"], "html")
        self.assertEqual(result["evidence_type"], "comment_form_website_and_history")

    @patch("website_format_detector.requests.Session.get")
    def test_detects_html_from_new_comment_block_selectors(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            text="""
            <html>
              <head><title>Comment Entry Blocks</title></head>
              <body>
                <div id="comments">
                  <ol class="commentList">
                    <li id="comment-55">
                      <div class="commentContainer">
                        <div class="comment-entry">
                          Helpful post. Try <a href="https://example.com/tool">example tool</a>
                        </div>
                      </div>
                    </li>
                  </ol>
                </div>
              </body>
            </html>
            """,
            raise_for_status=Mock(),
        )

        result = WebsiteFormatDetector().analyze_website("https://example.com/comment-entry")

        self.assertEqual(result["recommended_format"], "html")
        self.assertEqual(result["evidence_type"], "historical_anchor_text_link")

    @patch("website_format_detector.requests.Session.get")
    def test_skips_known_profile_like_urls_before_fetching(self, mock_get):
        result = WebsiteFormatDetector().analyze_website("https://myanimelist.net/profile/Akizuki_Airi")

        self.assertEqual(result["recommended_format"], "unknown")
        self.assertEqual(result["evidence_type"], "skip_non_article_page")
        mock_get.assert_not_called()

    @patch("website_format_detector.requests.Session.get")
    def test_detects_html_for_blogger_comment_system(self, mock_get):
        mock_get.return_value = Mock(
            status_code=200,
            text="""
            <html>
              <head><title>Blogger Comments</title></head>
              <body>
                <article>Post body</article>
                <iframe src="https://www.blogger.com/comment/frame/123"></iframe>
                <footer>Powered by Blogger</footer>
              </body>
            </html>
            """,
            raise_for_status=Mock(),
        )

        result = WebsiteFormatDetector().analyze_website(
            "https://example.com/entry-with-blogger-comments"
        )

        self.assertEqual(result["recommended_format"], "html")
        self.assertEqual(result["evidence_type"], "blogger_comment_system")

    def test_analyze_page_capability_marks_conflict_for_autolink_upgraded_to_html(self):
        detector = WebsiteFormatDetector()
        page = Mock()

        with patch.object(
            detector,
            "analyze_website",
            return_value={
                "recommended_format": "plain_text_autolink",
                "evidence_type": "historical_autolink",
                "confidence": 0.92,
            },
        ), patch.object(
            detector,
            "_prepare_probe_page",
            return_value=None,
        ), patch.object(
            detector,
            "analyze_runtime_page",
            return_value={
                "recommended_format": "html",
                "evidence_type": "runtime_contenteditable",
                "confidence": 0.9,
            },
        ), patch.object(
            detector,
            "analyze_vision_page",
            return_value={
                "recommended_format": "html",
                "evidence_type": "vision_format_capability",
                "confidence": 0.93,
            },
        ):
            result = detector.analyze_page_capability(page, "https://example.com/post")

        self.assertEqual(result["status"], "conflict")
        self.assertTrue(result["vision_used"])
        self.assertEqual(result["final_result"]["recommended_format"], "html")

    def test_analyze_page_capability_marks_conflict_when_static_html_runtime_unknown(self):
        detector = WebsiteFormatDetector()
        page = Mock()

        with patch.object(
            detector,
            "analyze_website",
            return_value={
                "recommended_format": "html",
                "evidence_type": "editor_html",
                "confidence": 0.5,
            },
        ), patch.object(
            detector,
            "_prepare_probe_page",
            return_value=None,
        ), patch.object(
            detector,
            "analyze_runtime_page",
            return_value={
                "recommended_format": "unknown",
                "evidence_type": "runtime_unknown",
                "confidence": 0.0,
            },
        ):
            result = detector.analyze_page_capability(
                page,
                "https://example.com/post",
                enable_vision=False,
            )

        self.assertEqual(result["status"], "conflict")
        self.assertEqual(result["final_result"]["recommended_format"], "html")

    def test_analyze_page_capability_marks_conflict_when_runtime_and_vision_disagree(self):
        detector = WebsiteFormatDetector()
        page = Mock()

        with patch.object(
            detector,
            "analyze_website",
            return_value={
                "recommended_format": "plain_text_autolink",
                "evidence_type": "historical_autolink",
                "confidence": 0.92,
            },
        ), patch.object(
            detector,
            "_prepare_probe_page",
            return_value=None,
        ), patch.object(
            detector,
            "analyze_runtime_page",
            return_value={
                "recommended_format": "html",
                "evidence_type": "runtime_contenteditable",
                "confidence": 0.9,
            },
        ), patch.object(
            detector,
            "analyze_vision_page",
            return_value={
                "recommended_format": "plain_text",
                "evidence_type": "vision_plain_text",
                "confidence": 0.7,
            },
        ):
            result = detector.analyze_page_capability(page, "https://example.com/post")

        self.assertEqual(result["status"], "conflict")
        self.assertTrue(result["vision_used"])
        self.assertEqual(result["final_result"]["recommended_format"], "html")

    def test_analyze_page_capability_ignores_vision_timeout_for_conflict(self):
        detector = WebsiteFormatDetector()
        page = Mock()

        with patch.object(
            detector,
            "analyze_website",
            return_value={
                "recommended_format": "unknown",
                "evidence_type": "unknown",
                "confidence": 0.0,
            },
        ), patch.object(
            detector,
            "_prepare_probe_page",
            return_value=None,
        ), patch.object(
            detector,
            "analyze_runtime_page",
            return_value={
                "recommended_format": "html",
                "evidence_type": "runtime_blogger_iframe",
                "confidence": 0.86,
            },
        ), patch.object(
            detector,
            "analyze_vision_page",
            return_value={
                "recommended_format": "unknown",
                "evidence_type": "vision_api_error",
                "confidence": 0.0,
            },
        ):
            result = detector.analyze_page_capability(page, "https://example.com/post")

        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["vision_used"])
        self.assertFalse(result["conflict"])


if __name__ == "__main__":
    unittest.main()
