import unittest

from sync_reporting_workbook import (
    _filter_rows_by_exclusions,
    _normalize_excluded_domains,
    _normalize_excluded_urls,
)


class SyncReportingWorkbookTests(unittest.TestCase):
    def test_filter_rows_by_exact_source_url_matches_rich_text(self):
        rows = [
            {
                "来源链接": [
                    {
                        "cellPosition": None,
                        "link": "https://spellbee.org/?utm_source=test",
                        "text": "https://spellbee.org/?utm_source=test",
                        "type": "url",
                    }
                ]
            },
            {"来源链接": "https://example.com/post"},
        ]

        filtered = _filter_rows_by_exclusions(
            rows,
            _normalize_excluded_domains([]),
            _normalize_excluded_urls(["https://spellbee.org/"]),
        )

        self.assertEqual(filtered, [{"来源链接": "https://example.com/post"}])

    def test_filter_rows_by_domain_and_exact_url_can_coexist(self):
        rows = [
            {"来源链接": "https://search.yahoo.com/search?p=test"},
            {"来源链接": "https://quordly.com/"},
            {"来源链接": "https://quordly.com/post/keep-me"},
        ]

        filtered = _filter_rows_by_exclusions(
            rows,
            _normalize_excluded_domains(["search.yahoo.com"]),
            _normalize_excluded_urls(["https://quordly.com/"]),
        )

        self.assertEqual(filtered, [{"来源链接": "https://quordly.com/post/keep-me"}])


if __name__ == "__main__":
    unittest.main()
