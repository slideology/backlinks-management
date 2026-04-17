import unittest
from datetime import datetime
from unittest.mock import patch

from backlink_state import (
    EXECUTION_MODE_AGENT,
    EXECUTION_MODE_CLASSIC,
    STATUS_BLOCKED,
    STATUS_COOLDOWN,
    STATUS_IN_PROGRESS,
    STATUS_NOT_STARTED,
    STATUS_PENDING_RETRY,
    STATUS_SUCCESS,
    build_source_master_rows,
    build_target_site_rows,
    reconcile_status_rows,
    select_daily_tasks,
)


class BacklinkStateTests(unittest.TestCase):
    def test_build_target_site_rows_falls_back_to_bootstrap_email_when_current_is_blank(self):
        rows = build_target_site_rows(
            existing_rows=[
                {
                    "站点标识": "bearclicker.net",
                    "目标网站": "https://bearclicker.net/",
                    "默认锚文本": "bear clicker",
                    "网站说明": "",
                    "联系邮箱": "",
                    "优先级": "1",
                    "冷却天数": "30",
                    "每日成功目标": "10",
                    "是否启用": "是",
                    "创建时间": "2026-03-20 08:00:00",
                }
            ],
            bootstrap_targets=[
                {
                    "url": "https://bearclicker.net/",
                    "anchor_text": "bear clicker",
                    "description": "desc",
                    "email": "slideology0816@gmail.com",
                    "active": True,
                }
            ],
        )

        by_site = {row["站点标识"]: row for row in rows}
        self.assertEqual(by_site["bearclicker.net"]["联系邮箱"], "slideology0816@gmail.com")

    def test_reconcile_sets_next_site_to_cooldown_after_previous_success(self):
        targets = build_target_site_rows(
            bootstrap_targets=[
                {"url": "https://bearclicker.net/", "anchor_text": "bear", "active": True},
                {"url": "https://nanobananaimage.com/", "anchor_text": "nano", "active": True},
            ]
        )
        targets[0]["站点标识"] = "b"
        targets[0]["优先级"] = "1"
        targets[1]["站点标识"] = "n"
        targets[1]["优先级"] = "2"

        status_rows = reconcile_status_rows(
            existing_status_rows=[
                {
                    "来源链接": "https://example.com/post",
                    "来源标题": "Example",
                    "根域名": "example.com",
                    "页面评分": "30",
                    "目标站标识": "b",
                    "目标网站": "https://bearclicker.net/",
                    "状态": "成功",
                    "最近成功时间": "2026-03-20 09:00:00",
                    "最后更新时间": "2026-03-20 09:00:00",
                }
            ],
            target_rows=targets,
            library_rows=[
                {
                    "来源标题": "Example",
                    "来源链接": "https://example.com/post",
                    "根域名": "example.com",
                    "页面评分": "30",
                }
            ],
            legacy_history_rows=[],
            now=datetime(2026, 3, 20, 12, 0, 0),
        )

        by_site = {row["目标站标识"]: row for row in status_rows}
        self.assertEqual(by_site["bearclicker.net"]["状态"], STATUS_SUCCESS)
        self.assertEqual(by_site["nanobananaimage.org"]["状态"], STATUS_COOLDOWN)
        self.assertEqual(by_site["nanobananaimage.org"]["下次可发时间"], "2026-03-21 09:00:00")

    def test_reconcile_allows_any_site_to_start_when_no_other_site_has_succeeded(self):
        targets = [
            {
                "站点标识": "b",
                "目标网站": "https://bearclicker.net/",
                "默认锚文本": "bear",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": "1",
                "冷却天数": "30",
                "每日成功目标": "10",
                "是否启用": "是",
                "创建时间": "2026-03-20 08:00:00",
            },
            {
                "站点标识": "n",
                "目标网站": "https://nanobananaimage.com/",
                "默认锚文本": "nano",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": "2",
                "冷却天数": "30",
                "每日成功目标": "10",
                "是否启用": "是",
                "创建时间": "2026-03-20 08:00:00",
            },
        ]

        status_rows = reconcile_status_rows(
            existing_status_rows=[],
            target_rows=targets,
            library_rows=[
                {
                    "来源标题": "Example",
                    "来源链接": "https://example.com/post",
                    "根域名": "example.com",
                    "页面评分": "30",
                }
            ],
            legacy_history_rows=[],
            now=datetime(2026, 3, 20, 12, 0, 0),
        )

        by_site = {row["目标站标识"]: row for row in status_rows}
        self.assertEqual(by_site["bearclicker.net"]["状态"], STATUS_NOT_STARTED)
        self.assertEqual(by_site["nanobananaimage.org"]["状态"], STATUS_NOT_STARTED)

    def test_reconcile_recomputes_existing_cooldown_when_target_days_change(self):
        targets = [
            {
                "站点标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "默认锚文本": "bear",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": "1",
                "冷却天数": "1",
                "每日成功目标": "10",
                "是否启用": "是",
                "创建时间": "2026-03-20 08:00:00",
            },
            {
                "站点标识": "nanobananaimage.org",
                "目标网站": "https://nanobananaimage.org/nano-banana-2",
                "默认锚文本": "nano",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": "2",
                "冷却天数": "1",
                "每日成功目标": "10",
                "是否启用": "是",
                "创建时间": "2026-03-20 08:00:00",
            },
        ]

        status_rows = reconcile_status_rows(
            existing_status_rows=[
                {
                    "来源链接": "https://example.com/post",
                    "来源标题": "Example",
                    "根域名": "example.com",
                    "页面评分": "30",
                    "目标站标识": "bearclicker.net",
                    "目标网站": "https://bearclicker.net/",
                    "状态": STATUS_SUCCESS,
                    "最近成功时间": "2026-03-20 09:00:00",
                    "最后更新时间": "2026-03-20 09:00:00",
                },
                {
                    "来源链接": "https://example.com/post",
                    "来源标题": "Example",
                    "根域名": "example.com",
                    "页面评分": "30",
                    "目标站标识": "nanobananaimage.org",
                    "目标网站": "https://nanobananaimage.org/nano-banana-2",
                    "状态": STATUS_COOLDOWN,
                    "下次可发时间": "2026-04-19 09:00:00",
                    "最后更新时间": "2026-03-20 09:00:00",
                },
            ],
            target_rows=targets,
            library_rows=[
                {
                    "来源标题": "Example",
                    "来源链接": "https://example.com/post",
                    "根域名": "example.com",
                    "页面评分": "30",
                }
            ],
            legacy_history_rows=[],
            now=datetime(2026, 3, 20, 12, 0, 0),
        )

        by_site = {row["目标站标识"]: row for row in status_rows}
        self.assertEqual(by_site["nanobananaimage.org"]["状态"], STATUS_COOLDOWN)
        self.assertEqual(by_site["nanobananaimage.org"]["下次可发时间"], "2026-03-21 09:00:00")

    def test_reconcile_treats_legacy_success_without_timestamp_as_completed_and_unlocks_next_site(self):
        targets = [
            {
                "站点标识": "b",
                "目标网站": "https://bearclicker.net/",
                "默认锚文本": "bear",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": "1",
                "冷却天数": "30",
                "每日成功目标": "10",
                "是否启用": "是",
                "创建时间": "2026-03-20 08:00:00",
            },
            {
                "站点标识": "n",
                "目标网站": "https://nanobananaimage.com/",
                "默认锚文本": "nano",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": "2",
                "冷却天数": "30",
                "每日成功目标": "10",
                "是否启用": "是",
                "创建时间": "2026-03-20 08:00:00",
            },
        ]

        status_rows = reconcile_status_rows(
            existing_status_rows=[],
            target_rows=targets,
            library_rows=[
                {
                    "来源标题": "Example",
                    "来源链接": "https://example.com/post",
                    "根域名": "example.com",
                    "页面评分": "30",
                }
            ],
            legacy_history_rows=[
                {
                    "来源标题": "Example",
                    "来源链接": "https://example.com/post",
                    "根域名": "example.com",
                    "目标站标识": "b",
                    "成功时间": "",
                    "来源标签页": "历史标签页",
                    "来源行号": "1",
                    "页面评分": "30",
                }
            ],
            now=datetime(2026, 3, 20, 12, 0, 0),
        )

        by_site = {row["目标站标识"]: row for row in status_rows}
        self.assertEqual(by_site["bearclicker.net"]["状态"], STATUS_SUCCESS)
        self.assertEqual(by_site["bearclicker.net"]["最近成功时间"], "")
        self.assertEqual(by_site["nanobananaimage.org"]["状态"], STATUS_NOT_STARTED)
        self.assertEqual(by_site["nanobananaimage.org"]["下次可发时间"], "")

    def test_reconcile_applies_cooldown_from_any_other_site_success(self):
        targets = [
            {
                "站点标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "默认锚文本": "bear",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": "1",
                "冷却天数": "1",
                "每日成功目标": "10",
                "是否启用": "是",
                "创建时间": "2026-03-20 08:00:00",
            },
            {
                "站点标识": "nanobananaimage.org",
                "目标网站": "https://nanobananaimage.org/nano-banana-2",
                "默认锚文本": "nano",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": "2",
                "冷却天数": "1",
                "每日成功目标": "10",
                "是否启用": "是",
                "创建时间": "2026-03-20 08:00:00",
            },
        ]

        status_rows = reconcile_status_rows(
            existing_status_rows=[
                {
                    "来源链接": "https://example.com/post",
                    "来源标题": "Example",
                    "根域名": "example.com",
                    "页面评分": "30",
                    "目标站标识": "nanobananaimage.org",
                    "目标网站": "https://nanobananaimage.org/nano-banana-2",
                    "状态": STATUS_SUCCESS,
                    "最近成功时间": "2026-03-20 09:00:00",
                    "最后更新时间": "2026-03-20 09:00:00",
                }
            ],
            target_rows=targets,
            library_rows=[
                {
                    "来源标题": "Example",
                    "来源链接": "https://example.com/post",
                    "根域名": "example.com",
                    "页面评分": "30",
                }
            ],
            legacy_history_rows=[],
            now=datetime(2026, 3, 20, 12, 0, 0),
        )

        by_site = {row["目标站标识"]: row for row in status_rows}
        self.assertEqual(by_site["nanobananaimage.org"]["状态"], STATUS_SUCCESS)
        self.assertEqual(by_site["bearclicker.net"]["状态"], STATUS_COOLDOWN)
        self.assertEqual(by_site["bearclicker.net"]["下次可发时间"], "2026-03-21 09:00:00")

    @patch("backlink_state._load_agent_assist_runtime", return_value={"same_domain_daily_limit": 0})
    def test_select_daily_tasks_prioritizes_sources_posted_on_other_sites(self, _mock_cfg):
        targets = [
            {
                "站点标识": "b",
                "目标网站": "https://bearclicker.net/",
                "默认锚文本": "bear",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": "1",
                "冷却天数": "30",
                "每日成功目标": "1",
                "是否启用": "是",
                "创建时间": "2026-03-20 08:00:00",
            },
            {
                "站点标识": "n",
                "目标网站": "https://nanobananaimage.com/",
                "默认锚文本": "nano",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": "2",
                "冷却天数": "30",
                "每日成功目标": "2",
                "是否启用": "是",
                "创建时间": "2026-03-20 08:00:00",
            },
        ]
        status_rows = [
            {
                "来源链接": "https://posted.com/a",
                "来源标题": "Posted",
                "根域名": "posted.com",
                "页面评分": "50",
                "目标站标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "状态": STATUS_SUCCESS,
                "最近成功时间": "2026-02-01 09:00:00",
                "最后尝试时间": "",
                "最近失败时间": "",
                "最近失败原因": "",
                "下次可发时间": "",
                "成功链接": "",
                "当前评论内容": "",
                "当前评论内容中文": "",
                "当前锚文本": "",
                "关键词": "",
                "链接格式": "",
                "来源类型": "",
                "有网址字段": "",
                "有验证码": "",
                "最后更新时间": "",
            },
            {
                "来源链接": "https://posted.com/a",
                "来源标题": "Posted",
                "根域名": "posted.com",
                "页面评分": "50",
                "目标站标识": "nanobananaimage.org",
                "目标网站": "https://nanobananaimage.org/nano-banana-2",
                "状态": STATUS_NOT_STARTED,
                "最近成功时间": "",
                "最后尝试时间": "",
                "最近失败时间": "",
                "最近失败原因": "",
                "下次可发时间": "",
                "成功链接": "",
                "当前评论内容": "",
                "当前评论内容中文": "",
                "当前锚文本": "",
                "关键词": "",
                "链接格式": "",
                "来源类型": "",
                "有网址字段": "",
                "有验证码": "",
                "最后更新时间": "",
            },
            {
                "来源链接": "https://fresh.com/a",
                "来源标题": "Fresh",
                "根域名": "fresh.com",
                "页面评分": "10",
                "目标站标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "状态": STATUS_NOT_STARTED,
                "最近成功时间": "",
                "最后尝试时间": "",
                "最近失败时间": "",
                "最近失败原因": "",
                "下次可发时间": "",
                "成功链接": "",
                "当前评论内容": "",
                "当前评论内容中文": "",
                "当前锚文本": "",
                "关键词": "",
                "链接格式": "",
                "来源类型": "",
                "有网址字段": "",
                "有验证码": "",
                "最后更新时间": "",
            },
            {
                "来源链接": "https://fresh.com/a",
                "来源标题": "Fresh",
                "根域名": "fresh.com",
                "页面评分": "10",
                "目标站标识": "nanobananaimage.org",
                "目标网站": "https://nanobananaimage.org/nano-banana-2",
                "状态": STATUS_NOT_STARTED,
                "最近成功时间": "",
                "最后尝试时间": "",
                "最近失败时间": "",
                "最近失败原因": "",
                "下次可发时间": "",
                "成功链接": "",
                "当前评论内容": "",
                "当前评论内容中文": "",
                "当前锚文本": "",
                "关键词": "",
                "链接格式": "",
                "来源类型": "",
                "有网址字段": "",
                "有验证码": "",
                "最后更新时间": "",
            },
        ]

        selected, updated_rows, meta = select_daily_tasks(status_rows, targets, now=datetime(2026, 3, 20, 12, 0, 0))
        selected_n = [task for task in selected if task["target"]["site_key"] == "nanobananaimage.org"]

        self.assertEqual(len(selected_n), 2)
        self.assertEqual(selected_n[0]["status_row"]["来源链接"], "https://posted.com/a")
        self.assertEqual(selected_n[0]["status_row"]["状态"], STATUS_IN_PROGRESS)

    def test_select_daily_tasks_prefers_not_started_before_pending_retry_within_same_bucket(self):
        targets = [
            {
                "站点标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "默认锚文本": "bear",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": "1",
                "冷却天数": "30",
                "每日成功目标": "2",
                "是否启用": "是",
                "创建时间": "2026-03-20 08:00:00",
            }
        ]
        status_rows = [
            {
                "来源链接": "https://retry.com/a",
                "来源标题": "Retry",
                "根域名": "retry.com",
                "页面评分": "99",
                "目标站标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "状态": STATUS_PENDING_RETRY,
                "最近成功时间": "",
                "最后尝试时间": "2026-03-23 10:00:00",
                "最近失败时间": "2026-03-23 10:00:00",
                "最近失败原因": "Timeout",
                "下次可发时间": "",
                "成功链接": "",
                "当前评论内容": "",
                "当前评论内容中文": "",
                "当前锚文本": "",
                "关键词": "",
                "链接格式": "",
                "来源类型": "",
                "有网址字段": "",
                "有验证码": "",
                "最后更新时间": "2026-03-23 10:00:00",
            },
            {
                "来源链接": "https://fresh.com/a",
                "来源标题": "Fresh",
                "根域名": "fresh.com",
                "页面评分": "10",
                "目标站标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "状态": STATUS_NOT_STARTED,
                "最近成功时间": "",
                "最后尝试时间": "",
                "最近失败时间": "",
                "最近失败原因": "",
                "下次可发时间": "",
                "成功链接": "",
                "当前评论内容": "",
                "当前评论内容中文": "",
                "当前锚文本": "",
                "关键词": "",
                "链接格式": "",
                "来源类型": "",
                "有网址字段": "",
                "有验证码": "",
                "最后更新时间": "",
            },
        ]

        selected, _, _ = select_daily_tasks(status_rows, targets, now=datetime(2026, 3, 24, 12, 0, 0))

        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0]["status_row"]["来源链接"], "https://fresh.com/a")
        self.assertEqual(selected[1]["status_row"]["来源链接"], "https://retry.com/a")

    def test_select_daily_tasks_prefers_all_not_started_before_any_pending_retry(self):
        targets = [
            {
                "站点标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "默认锚文本": "bear",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": "1",
                "冷却天数": "1",
                "每日成功目标": "2",
                "是否启用": "是",
                "创建时间": "2026-03-20 08:00:00",
            }
        ]
        status_rows = [
            {
                "来源链接": "https://posted-retry.com/a",
                "来源标题": "Posted Retry",
                "根域名": "posted-retry.com",
                "页面评分": "99",
                "目标站标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "状态": STATUS_PENDING_RETRY,
                "最近成功时间": "",
                "最后尝试时间": "2026-03-23 10:00:00",
                "最近失败时间": "2026-03-23 10:00:00",
                "最近失败原因": "Timeout",
                "下次可发时间": "",
                "成功链接": "",
                "当前评论内容": "",
                "当前评论内容中文": "",
                "当前锚文本": "",
                "关键词": "",
                "链接格式": "",
                "来源类型": "",
                "有网址字段": "",
                "有验证码": "",
                "最后更新时间": "2026-03-23 10:00:00",
            },
            {
                "来源链接": "https://posted-retry.com/a",
                "来源标题": "Posted Retry",
                "根域名": "posted-retry.com",
                "页面评分": "99",
                "目标站标识": "nanobananaimage.org",
                "目标网站": "https://nanobananaimage.org/nano-banana-2",
                "状态": STATUS_SUCCESS,
                "最近成功时间": "2026-03-18 09:00:00",
                "最后尝试时间": "",
                "最近失败时间": "",
                "最近失败原因": "",
                "下次可发时间": "",
                "成功链接": "",
                "当前评论内容": "",
                "当前评论内容中文": "",
                "当前锚文本": "",
                "关键词": "",
                "链接格式": "",
                "来源类型": "",
                "有网址字段": "",
                "有验证码": "",
                "最后更新时间": "",
            },
            {
                "来源链接": "https://fresh-prioritized.com/a",
                "来源标题": "Fresh Prioritized",
                "根域名": "fresh-prioritized.com",
                "页面评分": "1",
                "目标站标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "状态": STATUS_NOT_STARTED,
                "最近成功时间": "",
                "最后尝试时间": "",
                "最近失败时间": "",
                "最近失败原因": "",
                "下次可发时间": "",
                "成功链接": "",
                "当前评论内容": "",
                "当前评论内容中文": "",
                "当前锚文本": "",
                "关键词": "",
                "链接格式": "",
                "来源类型": "",
                "有网址字段": "",
                "有验证码": "",
                "最后更新时间": "",
            },
            {
                "来源链接": "https://fresh.com/a",
                "来源标题": "Fresh",
                "根域名": "fresh.com",
                "页面评分": "10",
                "目标站标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "状态": STATUS_NOT_STARTED,
                "最近成功时间": "",
                "最后尝试时间": "",
                "最近失败时间": "",
                "最近失败原因": "",
                "下次可发时间": "",
                "成功链接": "",
                "当前评论内容": "",
                "当前评论内容中文": "",
                "当前锚文本": "",
                "关键词": "",
                "链接格式": "",
                "来源类型": "",
                "有网址字段": "",
                "有验证码": "",
                "最后更新时间": "",
            },
        ]

        selected, _, _ = select_daily_tasks(status_rows, targets, now=datetime(2026, 3, 24, 12, 0, 0))

        self.assertEqual(len(selected), 2)
        self.assertEqual(
            {task["status_row"]["来源链接"] for task in selected},
            {"https://fresh-prioritized.com/a", "https://fresh.com/a"},
        )

    def test_select_daily_tasks_skips_domain_cooldown_rows(self):
        targets = [
            {
                "站点标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "默认锚文本": "bear",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": "1",
                "冷却天数": "1",
                "每日成功目标": "1",
                "是否启用": "是",
                "创建时间": "2026-03-20 08:00:00",
            }
        ]
        status_rows = [
            {
                "来源链接": "https://cooling.com/a",
                "来源标题": "Cooling",
                "根域名": "cooling.com",
                "页面评分": "10",
                "目标站标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "状态": STATUS_NOT_STARTED,
                "域名冷却至": "2026-03-24 18:00:00",
            },
            {
                "来源链接": "https://ready.com/a",
                "来源标题": "Ready",
                "根域名": "ready.com",
                "页面评分": "9",
                "目标站标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "状态": STATUS_NOT_STARTED,
                "域名冷却至": "",
            },
        ]

        selected, _, _ = select_daily_tasks(status_rows, targets, now=datetime(2026, 3, 24, 12, 0, 0))

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["status_row"]["来源链接"], "https://ready.com/a")

    def test_select_daily_tasks_skips_same_day_agent_assisted_retry(self):
        targets = [
            {
                "站点标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "默认锚文本": "bear",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": "1",
                "冷却天数": "1",
                "每日成功目标": "1",
                "是否启用": "是",
                "创建时间": "2026-03-20 08:00:00",
            }
        ]
        status_rows = [
            {
                "来源链接": "https://agented.com/a",
                "来源标题": "Agented",
                "根域名": "agented.com",
                "页面评分": "20",
                "目标站标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "状态": STATUS_PENDING_RETRY,
                "执行模式": EXECUTION_MODE_AGENT,
                "最后尝试时间": "2026-03-24 08:00:00",
            },
            {
                "来源链接": "https://fresh.com/a",
                "来源标题": "Fresh",
                "根域名": "fresh.com",
                "页面评分": "10",
                "目标站标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "状态": STATUS_NOT_STARTED,
                "执行模式": EXECUTION_MODE_CLASSIC,
            },
        ]

        selected, _, _ = select_daily_tasks(status_rows, targets, now=datetime(2026, 3, 24, 12, 0, 0))

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["status_row"]["来源链接"], "https://fresh.com/a")

    @patch("backlink_state._load_agent_assist_runtime", return_value={"same_domain_daily_limit": 1})
    def test_select_daily_tasks_skips_domains_already_attempted_today(self, _mock_cfg):
        targets = [
            {
                "站点标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "默认锚文本": "bear",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": "1",
                "冷却天数": "1",
                "每日成功目标": "2",
                "是否启用": "是",
                "创建时间": "2026-03-20 08:00:00",
            }
        ]
        status_rows = [
            {
                "来源链接": "https://example.com/old-post",
                "来源标题": "Old",
                "根域名": "example.com",
                "页面评分": "30",
                "目标站标识": "bearclicker.net",
                "状态": STATUS_PENDING_RETRY,
                "最后尝试时间": "2026-03-24 08:00:00",
            },
            {
                "来源链接": "https://example.com/new-post",
                "来源标题": "New",
                "根域名": "example.com",
                "页面评分": "50",
                "目标站标识": "bearclicker.net",
                "状态": STATUS_NOT_STARTED,
                "最后尝试时间": "",
            },
            {
                "来源链接": "https://fresh.com/post",
                "来源标题": "Fresh",
                "根域名": "fresh.com",
                "页面评分": "10",
                "目标站标识": "bearclicker.net",
                "状态": STATUS_NOT_STARTED,
                "最后尝试时间": "",
            },
        ]

        selected, _, _ = select_daily_tasks(status_rows, targets, now=datetime(2026, 3, 24, 12, 0, 0))

        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["status_row"]["来源链接"], "https://fresh.com/post")

    @patch("backlink_state._load_agent_assist_runtime", return_value={"same_domain_daily_limit": 1})
    def test_select_daily_tasks_picks_only_one_row_per_domain_in_same_batch(self, _mock_cfg):
        targets = [
            {
                "站点标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "默认锚文本": "bear",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": "1",
                "冷却天数": "1",
                "每日成功目标": "3",
                "是否启用": "是",
                "创建时间": "2026-03-20 08:00:00",
            }
        ]
        status_rows = [
            {
                "来源链接": "https://example.com/post-a",
                "来源标题": "A",
                "根域名": "example.com",
                "页面评分": "30",
                "目标站标识": "bearclicker.net",
                "状态": STATUS_NOT_STARTED,
            },
            {
                "来源链接": "https://example.com/post-b",
                "来源标题": "B",
                "根域名": "example.com",
                "页面评分": "20",
                "目标站标识": "bearclicker.net",
                "状态": STATUS_NOT_STARTED,
            },
            {
                "来源链接": "https://fresh.com/post",
                "来源标题": "Fresh",
                "根域名": "fresh.com",
                "页面评分": "10",
                "目标站标识": "bearclicker.net",
                "状态": STATUS_NOT_STARTED,
            },
        ]

        selected, _, _ = select_daily_tasks(status_rows, targets, now=datetime(2026, 3, 24, 12, 0, 0))

        self.assertEqual(
            {task["status_row"]["来源链接"] for task in selected},
            {"https://example.com/post-a", "https://fresh.com/post"},
        )

    def test_build_source_master_rows_expands_per_site_columns(self):
        targets = [
            {"站点标识": "b", "目标网站": "https://bearclicker.net/", "优先级": "1", "是否启用": "是"},
            {"站点标识": "n", "目标网站": "https://nanobananaimage.com/", "优先级": "2", "是否启用": "是"},
        ]
        rows = build_source_master_rows(
            [
                {
                    "来源标题": "Example",
                    "来源链接": "https://example.com/post",
                    "根域名": "example.com",
                    "页面评分": "30",
                    "目标站标识": "b",
                    "目标网站": "https://bearclicker.net/",
                    "状态": STATUS_SUCCESS,
                    "最近成功时间": "2026-03-20 09:00:00",
                    "最后尝试时间": "2026-03-20 09:00:00",
                    "最近失败时间": "",
                    "最近失败原因": "",
                    "下次可发时间": "",
                    "成功链接": "",
                    "当前评论内容": "",
                    "当前评论内容中文": "",
                    "当前锚文本": "",
                    "关键词": "",
                    "链接格式": "",
                    "来源类型": "",
                    "有网址字段": "",
                    "有验证码": "",
                    "最后更新时间": "2026-03-20 09:00:00",
                },
                {
                    "来源标题": "Example",
                    "来源链接": "https://example.com/post",
                    "根域名": "example.com",
                    "页面评分": "30",
                    "目标站标识": "n",
                    "目标网站": "https://nanobananaimage.com/",
                    "状态": STATUS_PENDING_RETRY,
                    "最近成功时间": "",
                    "最后尝试时间": "2026-04-21 10:00:00",
                    "最近失败时间": "2026-04-21 10:00:00",
                    "最近失败原因": "Timeout",
                    "下次可发时间": "2026-04-19 09:00:00",
                    "成功链接": "",
                    "当前评论内容": "",
                    "当前评论内容中文": "",
                    "当前锚文本": "",
                    "关键词": "",
                    "链接格式": "",
                    "来源类型": "",
                    "有网址字段": "",
                    "有验证码": "",
                    "最后更新时间": "2026-04-21 10:00:00",
                },
            ],
            targets,
        )

        self.assertEqual(rows[0]["当前应发站点"], "n")
        self.assertEqual(rows[0]["b_状态"], STATUS_SUCCESS)
        self.assertEqual(rows[0]["n_最后失败原因"], "Timeout")

    def test_build_source_master_rows_preserves_existing_format_probe_fields(self):
        targets = [
            {"站点标识": "bearclicker.net", "目标网站": "https://bearclicker.net/", "优先级": "1", "是否启用": "是"},
        ]
        rows = build_source_master_rows(
            [
                {
                    "来源标题": "Example",
                    "来源链接": "https://example.com/post",
                    "根域名": "example.com",
                    "页面评分": "30",
                    "目标站标识": "bearclicker.net",
                    "目标网站": "https://bearclicker.net/",
                    "状态": STATUS_PENDING_RETRY,
                    "最近成功时间": "",
                    "最后尝试时间": "",
                    "最近失败时间": "",
                    "最近失败原因": "Timeout",
                    "下次可发时间": "",
                    "成功链接": "",
                    "当前评论内容": "",
                    "当前评论内容中文": "",
                    "当前锚文本": "",
                    "关键词": "",
                    "链接格式": "",
                    "来源类型": "",
                    "有网址字段": "",
                    "有验证码": "",
                    "最后更新时间": "2026-03-23 12:00:00",
                }
            ],
            targets,
            existing_source_rows=[
                {
                    "来源链接": "https://example.com/post",
                    "初始链接格式": "unknown",
                    "最终链接格式": "html",
                    "格式检测阶段": "vision_probe",
                    "格式检测证据": "vision_format_capability",
                    "格式检测置信度": "0.91",
                    "是否需要登录": "否",
                    "是否支持Google登录": "是",
                    "评论区是否存在": "是",
                    "历史外链验证结果": "bearclicker.net",
                    "历史审计状态": "completed",
                    "格式检测状态": "completed",
                }
            ],
        )

        self.assertEqual(rows[0]["初始链接格式"], "unknown")
        self.assertEqual(rows[0]["最终链接格式"], "html")
        self.assertEqual(rows[0]["格式检测阶段"], "vision_probe")
        self.assertEqual(rows[0]["是否需要登录"], "否")
        self.assertEqual(rows[0]["是否支持Google登录"], "是")
        self.assertEqual(rows[0]["评论区是否存在"], "是")
        self.assertEqual(rows[0]["历史外链验证结果"], "bearclicker.net")

    @patch("backlink_state.AgentMemory")
    def test_reconcile_populates_agent_execution_fields_from_memory(self, mock_memory_cls):
        fake_memory = mock_memory_cls.return_value
        fake_memory.get_site_profile.return_value = {
            "best_strategy": "vision",
            "consecutive_failures": 3,
            "recent_failure_category": "vision_unavailable",
            "recent_failure_reason": "Vision API 调用失败: timed out",
            "blacklisted": False,
            "temporarily_blacklisted": False,
            "cooldown_until": "2026-03-24T18:00:00",
        }

        targets = [
            {
                "站点标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "默认锚文本": "bear",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": "1",
                "冷却天数": "1",
                "每日成功目标": "10",
                "是否启用": "是",
                "创建时间": "2026-03-20 08:00:00",
            }
        ]

        status_rows = reconcile_status_rows(
            existing_status_rows=[
                {
                    "来源链接": "https://example.com/post",
                    "来源标题": "Example",
                    "根域名": "example.com",
                    "页面评分": "30",
                    "目标站标识": "bearclicker.net",
                    "目标网站": "https://bearclicker.net/",
                    "状态": STATUS_PENDING_RETRY,
                    "最近失败时间": "2026-03-24 09:00:00",
                    "最近失败原因": "Vision API 调用失败: timed out",
                    "最后更新时间": "2026-03-24 09:00:00",
                }
            ],
            target_rows=targets,
            library_rows=[
                {
                    "来源标题": "Example",
                    "来源链接": "https://example.com/post",
                    "根域名": "example.com",
                    "页面评分": "30",
                }
            ],
            legacy_history_rows=[],
            now=datetime(2026, 3, 24, 12, 0, 0),
        )

        self.assertEqual(status_rows[0]["执行模式"], EXECUTION_MODE_AGENT)
        self.assertEqual(status_rows[0]["推荐策略"], "vision")
        self.assertEqual(status_rows[0]["最近失败分类"], "vision_unavailable")
        self.assertEqual(status_rows[0]["域名冷却至"], "2026-03-24 18:00:00")

    def test_build_target_rows_and_reconcile_migrate_short_aliases_to_domain_ids(self):
        targets = build_target_site_rows(
            existing_rows=[
                {
                    "站点标识": "b",
                    "目标网站": "https://bearclicker.net/",
                    "默认锚文本": "bear",
                    "优先级": "1",
                    "是否启用": "是",
                },
                {
                    "站点标识": "n",
                    "目标网站": "https://nanobananaimage.org/nano-banana-2",
                    "默认锚文本": "nano",
                    "优先级": "2",
                    "是否启用": "是",
                },
            ]
        )

        self.assertEqual(targets[0]["站点标识"], "bearclicker.net")
        self.assertEqual(targets[1]["站点标识"], "nanobananaimage.org")

    def test_reconcile_recovers_stale_in_progress_rows_on_same_day(self):
        targets = [
            {
                "站点标识": "bearclicker.net",
                "目标网站": "https://bearclicker.net/",
                "默认锚文本": "bear",
                "网站说明": "",
                "联系邮箱": "",
                "优先级": "1",
                "冷却天数": "30",
                "每日成功目标": "10",
                "是否启用": "是",
                "创建时间": "2026-03-23 08:00:00",
            }
        ]

        status_rows = reconcile_status_rows(
            existing_status_rows=[
                {
                    "来源链接": "https://example.com/post",
                    "来源标题": "Example",
                    "根域名": "example.com",
                    "页面评分": "30",
                    "目标站标识": "bearclicker.net",
                    "目标网站": "https://bearclicker.net/",
                    "状态": STATUS_IN_PROGRESS,
                    "最近成功时间": "",
                    "最后尝试时间": "2026-03-23 10:00:00",
                    "最近失败时间": "",
                    "最近失败原因": "",
                    "下次可发时间": "",
                    "成功链接": "",
                    "当前评论内容": "",
                    "当前评论内容中文": "",
                    "当前锚文本": "",
                    "关键词": "",
                    "链接格式": "",
                    "来源类型": "",
                    "有网址字段": "",
                    "有验证码": "",
                    "最后更新时间": "2026-03-23 10:00:00",
                }
            ],
            target_rows=targets,
            library_rows=[
                {
                    "来源标题": "Example",
                    "来源链接": "https://example.com/post",
                    "根域名": "example.com",
                    "页面评分": "30",
                }
            ],
            legacy_history_rows=[],
            now=datetime(2026, 3, 23, 10, 20, 0),
        )

        self.assertEqual(status_rows[0]["状态"], STATUS_PENDING_RETRY)


if __name__ == "__main__":
    unittest.main()
