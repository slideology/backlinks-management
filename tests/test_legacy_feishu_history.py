import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from legacy_feishu_history import (
    LegacyFeishuHistoryStore,
    extract_cell_url,
    fetch_legacy_history_records,
    get_root_domain,
    normalize_source_url,
    parse_page_ascore,
    parse_legacy_tab_rows,
    promoted_site_key_for_target,
)


class LegacyFeishuHistoryTests(unittest.TestCase):
    def test_normalize_source_url_removes_tracking_query_and_fragment(self):
        url = "HTTPS://Example.com/post?utm_source=google&id=7#comments"
        self.assertEqual(normalize_source_url(url), "https://example.com/post?id=7")

    def test_extract_cell_url_supports_feishu_rich_text(self):
        cell = [{"link": "https://example.com/post", "text": "https://example.com/post", "type": "url"}]
        self.assertEqual(extract_cell_url(cell), "https://example.com/post")

    def test_extract_cell_url_supports_stringified_feishu_rich_text(self):
        cell = "[{'cellPosition': None, 'link': 'https://example.com/post', 'text': 'https://example.com/post', 'type': 'url'}]"
        self.assertEqual(extract_cell_url(cell), "https://example.com/post")

    def test_parse_page_ascore_supports_numeric_text(self):
        self.assertEqual(parse_page_ascore("11"), 11.0)
        self.assertIsNone(parse_page_ascore(""))
        self.assertIsNone(parse_page_ascore("not-a-number"))

    def test_parse_legacy_tab_rows_only_emits_explicit_markers(self):
        rows = [
            ["Page ascore", "Source title", "Source url", None, None, "游戏站"],
            [
                37,
                "Example Title",
                [{"link": "https://www.example.com/post?utm_source=x", "text": "https://www.example.com/post?utm_source=x"}],
                "n20250923",
                "b",
                1,
            ],
            [
                16,
                "No markers",
                [{"link": "https://www.example.com/other", "text": "https://www.example.com/other"}],
                "",
                "",
                1,
            ],
            [
                9,
                "Too low",
                [{"link": "https://www.example.com/low", "text": "https://www.example.com/low"}],
                "n",
                "b",
                1,
            ],
        ]

        parsed = parse_legacy_tab_rows(rows, "wordle2.io", "sheet123")
        history_records, source_rows = parsed

        self.assertEqual(len(history_records), 2)
        self.assertEqual(history_records[0].promoted_site_key, "n")
        self.assertEqual(history_records[0].posted_date_raw, "20250923")
        self.assertEqual(history_records[0].source_url, "https://www.example.com/post")
        self.assertEqual(history_records[1].promoted_site_key, "b")
        self.assertEqual(len(source_rows), 2)
        self.assertEqual(source_rows[0].n_marker, "n20250923")
        self.assertEqual(source_rows[0].b_marker, "b")
        self.assertTrue(all(row.source_url != "https://www.example.com/low" for row in source_rows))

    def test_store_analyze_distinguishes_exact_and_domain_matches(self):
        rows = [
            ["Page ascore", "Source title", "Source url", None, None, "游戏站"],
            [
                37,
                "Example Title",
                [{"link": "https://www.example.com/post", "text": "https://www.example.com/post"}],
                "n",
                "b",
                1,
            ],
        ]
        records, _ = parse_legacy_tab_rows(rows, "wordle2.io", "sheet123")
        store = LegacyFeishuHistoryStore(records)

        exact = store.analyze("https://www.example.com/post#reply", "https://bearclicker.net/")
        self.assertEqual(exact["category"], "exact_duplicate_same_site")

        domain = store.analyze("https://www.example.com/another-post", "https://bearclicker.net/")
        self.assertEqual(domain["category"], "same_domain_same_site")
        self.assertEqual(domain["source_root_domain"], "example.com")

    def test_store_reports_missing_mapping_for_unknown_target(self):
        rows = [
            ["Page ascore", "Source title", "Source url", None, None, "游戏站"],
            [
                37,
                "Example Title",
                [{"link": "https://www.example.com/post", "text": "https://www.example.com/post"}],
                "n",
                "",
                1,
            ],
        ]
        records, _ = parse_legacy_tab_rows(rows, "wordle2.io", "sheet123")
        store = LegacyFeishuHistoryStore(records)
        result = store.analyze("https://www.example.com/post", "https://slideology.com")
        self.assertEqual(result["category"], "legacy_marker_missing_mapping")

    @patch("legacy_feishu_history.fetch_sheet_metainfo")
    def test_fetch_legacy_history_records_skips_summary_and_sheet54(self, mock_meta):
        mock_meta.return_value = {
            "sheets": [
                {"title": "汇总", "sheetId": "summary", "rowCount": 10},
                {"title": "Sheet54", "sheetId": "sheet54", "rowCount": 10},
                {"title": "wordle2.io", "sheetId": "main", "rowCount": 3},
            ]
        }
        client = Mock()
        client.read_range.return_value = [
            ["Page ascore", "Source title", "Source url", None, None, "游戏站"],
            [37, "Example", [{"link": "https://example.com/post", "text": "https://example.com/post"}], "n", "b", 1],
        ]

        records, source_rows = fetch_legacy_history_records(client, "token", {"汇总", "Sheet54"})

        self.assertEqual(len(records), 2)
        self.assertEqual(len(source_rows), 1)
        client.read_range.assert_called_once_with("main!A1:H3")

    @patch("legacy_feishu_history.fetch_legacy_history_records")
    def test_from_config_reads_cache_when_fresh(self, mock_fetch):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "cache.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "generated_at": 32503680000,
                        "records": [
                            {
                                "legacy_tab_title": "wordle2.io",
                                "source_url": "https://example.com/post",
                                "source_title": "Example",
                                "source_root_domain": "example.com",
                                "page_ascore": "37",
                                "promoted_site_key": "b",
                                "posted_flag": True,
                                "posted_date_raw": "",
                                "source_sheet_id": "sheet123",
                                "source_row": 2,
                            }
                        ],
                        "source_rows": [
                            {
                                "legacy_tab_title": "wordle2.io",
                                "source_url": "https://example.com/post",
                                "source_title": "Example",
                                "source_root_domain": "example.com",
                                "page_ascore": "37",
                                "tab_category": "1",
                                "n_marker": "n",
                                "b_marker": "b",
                                "extra_value": "",
                                "source_sheet_id": "sheet123",
                                "source_row": 2,
                            }
                        ],
                        "min_page_ascore": 10,
                    }
                ),
                encoding="utf-8",
            )
            config_path = Path(tmpdir) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "feishu": {
                            "enabled": True,
                            "app_id": "app",
                            "app_secret": "secret",
                            "auth_mode": "user",
                            "user_token_file": ".feishu_user_token.json",
                        },
                        "legacy_history": {
                            "enabled": True,
                            "cache_file": str(cache_path),
                            "cache_ttl_hours": 12,
                        },
                    }
                ),
                encoding="utf-8",
            )

            store = LegacyFeishuHistoryStore.from_config(str(config_path))

        self.assertEqual(len(store.records), 1)
        mock_fetch.assert_not_called()
        self.assertEqual(store.records[0].source_root_domain, "example.com")
        self.assertEqual(len(store.source_rows), 1)


if __name__ == "__main__":
    unittest.main()
