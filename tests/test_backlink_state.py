import unittest
from datetime import datetime

from backlink_state import (
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
        self.assertEqual(by_site["nanobananaimage.org"]["下次可发时间"], "2026-04-19 09:00:00")

    def test_reconcile_blocks_later_site_when_previous_not_done(self):
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
        self.assertEqual(by_site["nanobananaimage.org"]["状态"], STATUS_BLOCKED)

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

    def test_select_daily_tasks_prioritizes_sources_posted_on_other_sites(self):
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


if __name__ == "__main__":
    unittest.main()
