import unittest
from unittest.mock import patch
from datetime import datetime

import daily_run_orchestrator


class DailyRunOrchestratorTests(unittest.TestCase):
    def test_resolve_run_window_returns_matching_window(self):
        now = datetime(2026, 4, 24, 8, 30, 0)
        window = daily_run_orchestrator._resolve_run_window(
            now,
            [{"start": "08:00", "end": "10:00"}, {"start": "12:00", "end": "14:00"}],
        )
        self.assertIsNotNone(window)
        self.assertEqual(window["label"], "08:00-10:00")

    def test_resolve_run_window_returns_none_outside_windows(self):
        now = datetime(2026, 4, 24, 10, 30, 0)
        window = daily_run_orchestrator._resolve_run_window(
            now,
            [{"start": "08:00", "end": "10:00"}, {"start": "12:00", "end": "14:00"}],
        )
        self.assertIsNone(window)

    @patch("daily_run_orchestrator.create_webhook_sender", return_value=None)
    @patch("daily_run_orchestrator.run_once")
    @patch("daily_run_orchestrator.daily_scheduler.main")
    @patch("daily_run_orchestrator.sync_reporting_workbook")
    @patch("daily_run_orchestrator.datetime")
    def test_main_skips_when_outside_run_window(
        self,
        mock_datetime,
        mock_sync,
        mock_schedule,
        mock_run_once,
        _mock_sender,
    ):
        mock_datetime.now.return_value = datetime(2026, 4, 24, 10, 30, 0)

        daily_run_orchestrator.main()

        mock_schedule.assert_not_called()
        mock_run_once.assert_not_called()
        mock_sync.assert_not_called()

    @patch("daily_run_orchestrator.create_webhook_sender", return_value=None)
    @patch("daily_run_orchestrator.run_once", return_value={"success": [], "failed": []})
    @patch("daily_run_orchestrator.daily_scheduler.main", return_value={"selected_tasks": []})
    @patch("daily_run_orchestrator.sync_reporting_workbook", return_value={"status_rows": [], "targets": []})
    @patch("daily_run_orchestrator.datetime")
    def test_main_runs_inside_window(
        self,
        mock_datetime,
        _mock_sync,
        mock_schedule,
        mock_run_once,
        _mock_sender,
    ):
        mock_datetime.now.return_value = datetime(2026, 4, 24, 8, 30, 0)

        daily_run_orchestrator.main()

        mock_schedule.assert_called_once()
        mock_run_once.assert_called_once()

