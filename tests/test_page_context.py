import unittest
from unittest.mock import Mock, patch

from page_context import _extract_comment_candidates, fetch_page_context


class PageContextTests(unittest.TestCase):
    def test_extract_comment_candidates_filters_sidebar_noise(self):
        html = """
        <html lang="en">
          <body>
            <aside class="sidebar">
              <section class="recent comments">
                <div class="comment">Recent comments widget should not be treated as page discussion.</div>
              </section>
            </aside>
            <section id="comments">
              <article class="comment">
                <div class="comment-body">I liked the breakdown of the idli batter texture and the kurma pairing.</div>
              </article>
              <article class="comment">
                <div class="comment-body">The vegetable gravy tip made the breakfast combo easier to recreate at home.</div>
              </article>
            </section>
          </body>
        </html>
        """

        comments = _extract_comment_candidates(html)

        self.assertEqual(len(comments), 3)
        self.assertIn("idli batter texture", comments[1])
        self.assertIn("vegetable gravy tip", comments[2])
        self.assertNotIn("widget", " ".join(comments).lower())

    @patch("page_context.requests.get")
    @patch("ai_generator.summarize_comment_discussion")
    def test_fetch_page_context_returns_comment_summary(self, mock_summarize, mock_get):
        mock_summarize.return_value = "Readers are discussing the breakfast combo and texture tips."
        mock_get.return_value = Mock(
            status_code=200,
            text="""
            <html lang="en">
              <head>
                <title>Idli Kurma Recipe</title>
                <meta name="description" content="A South Indian breakfast pairing.">
              </head>
              <body>
                <article>
                  <p>This post explains how idli and kurma work together for breakfast.</p>
                </article>
                <section id="comments">
                  <article class="comment">
                    <div class="comment-body">The texture notes were especially helpful.</div>
                  </article>
                </section>
              </body>
            </html>
            """,
            raise_for_status=Mock(),
        )

        context = fetch_page_context("https://example.com/idli-kurma")

        self.assertEqual(context["language_code"], "en")
        self.assertEqual(context["title"], "Idli Kurma Recipe")
        self.assertEqual(context["comments_raw"], ["The texture notes were especially helpful."])
        self.assertEqual(
            context["comments_summary"],
            "Readers are discussing the breakfast combo and texture tips.",
        )
        mock_summarize.assert_called_once()


if __name__ == "__main__":
    unittest.main()
