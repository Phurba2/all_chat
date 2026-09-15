"""Tests for the automated fetch cycle (inbox/scheduler.py + commands)."""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from inbox import scheduler


class SchedulerCycleTests(TestCase):

    @mock.patch("inbox.scheduler.messenger.fetch_messages", return_value=2)
    @mock.patch("inbox.scheduler.messenger.is_configured", return_value=True)
    @mock.patch("inbox.scheduler.gmail.fetch_emails", return_value=3)
    @mock.patch("inbox.scheduler.gmail.is_configured", return_value=True)
    def test_cycle_returns_counts_when_both_configured(self, mock_gmail_cfg,
                                                       mock_gmail_fetch,
                                                       mock_msg_cfg,
                                                       mock_msg_fetch):
        report = scheduler.run_fetch_cycle()

        self.assertEqual(report["gmail"], 3)
        self.assertEqual(report["messenger"], 2)
        self.assertEqual(report["errors"], [])
        mock_gmail_fetch.assert_called_once_with()
        mock_msg_fetch.assert_called_once_with(include_outbound=True)

    @mock.patch("inbox.scheduler.messenger.fetch_messages", return_value=1)
    @mock.patch("inbox.scheduler.messenger.is_configured", return_value=True)
    @mock.patch("inbox.scheduler.gmail.fetch_emails")
    @mock.patch("inbox.scheduler.gmail.is_configured", return_value=False)
    def test_unconfigured_channel_is_skipped_not_failed(self, mock_gmail_cfg,
                                                        mock_gmail_fetch,
                                                        mock_msg_cfg,
                                                        mock_msg_fetch):
        report = scheduler.run_fetch_cycle()

        self.assertIsNone(report["gmail"])
        self.assertEqual(report["messenger"], 1)
        self.assertEqual(report["errors"], [])
        mock_gmail_fetch.assert_not_called()

    @mock.patch("inbox.scheduler.messenger.fetch_messages", return_value=4)
    @mock.patch("inbox.scheduler.messenger.is_configured", return_value=True)
    @mock.patch("inbox.scheduler.gmail.fetch_emails", side_effect=RuntimeError("IMAP down"))
    @mock.patch("inbox.scheduler.gmail.is_configured", return_value=True)
    def test_channel_failure_is_isolated(self, mock_gmail_cfg, mock_gmail_fetch,
                                         mock_msg_cfg, mock_msg_fetch):
        report = scheduler.run_fetch_cycle()

        self.assertIsNone(report["gmail"])
        self.assertEqual(report["messenger"], 4)
        self.assertEqual(len(report["errors"]), 1)
        self.assertIn("gmail", report["errors"][0])
        self.assertIn("IMAP down", report["errors"][0])

    @mock.patch("inbox.scheduler.messenger.fetch_messages")
    @mock.patch("inbox.scheduler.messenger.is_configured", return_value=False)
    @mock.patch("inbox.scheduler.gmail.fetch_emails", return_value=5)
    @mock.patch("inbox.scheduler.gmail.is_configured", return_value=True)
    def test_gmail_only_setup(self, mock_gmail_cfg, mock_gmail_fetch,
                              mock_msg_cfg, mock_msg_fetch):
        report = scheduler.run_fetch_cycle()

        self.assertEqual(report["gmail"], 5)
        self.assertIsNone(report["messenger"])
        self.assertEqual(report["errors"], [])
        mock_msg_fetch.assert_not_called()


class FetchAllCommandTests(TestCase):

    @mock.patch("inbox.management.commands.fetch_all.scheduler.run_fetch_cycle",
                return_value={"gmail": 3, "messenger": 0, "errors": []})
    def test_fetch_all_reports_counts(self, mock_cycle):
        out = StringIO()
        call_command("fetch_all", stdout=out)

        self.assertIn("gmail: stored 3", out.getvalue())
        self.assertIn("messenger: stored 0", out.getvalue())

    @mock.patch("inbox.management.commands.fetch_all.scheduler.run_fetch_cycle",
                return_value={"gmail": None, "messenger": None, "errors": []})
    def test_fetch_all_skipped_channels(self, mock_cycle):
        out = StringIO()
        call_command("fetch_all", stdout=out)

        self.assertIn("gmail: not configured", out.getvalue())
        self.assertIn("messenger: not configured", out.getvalue())

    @mock.patch("inbox.management.commands.fetch_all.scheduler.run_fetch_cycle",
                return_value={"gmail": 0, "messenger": 0, "errors": []})
    def test_fetch_all_quiet_silences_zero_and_skipped(self, mock_cycle):
        out = StringIO()
        call_command("fetch_all", "--quiet", stdout=out)

        self.assertEqual(out.getvalue().strip(), "")

    @mock.patch("inbox.management.commands.fetch_all.scheduler.run_fetch_cycle",
                return_value={"gmail": 2, "messenger": None,
                              "errors": ["messenger fetch failed: bad token"]})
    def test_fetch_all_quiet_still_shows_activity_and_errors(self, mock_cycle):
        out = StringIO()
        call_command("fetch_all", "--quiet", stdout=out)

        self.assertIn("gmail: stored 2", out.getvalue())
        self.assertIn("messenger fetch failed", out.getvalue())


class FetchGmailCommandTests(TestCase):

    @mock.patch("inbox.management.commands.fetch_gmail.scheduler.fetch_gmail_once", return_value=7)
    def test_fetch_gmail_reports_count(self, mock_fetch):
        out = StringIO()
        call_command("fetch_gmail", stdout=out)

        self.assertIn("Stored 7 new email(s).", out.getvalue())

    @mock.patch("inbox.management.commands.fetch_gmail.scheduler.fetch_gmail_once", return_value=None)
    def test_fetch_gmail_errors_when_not_configured(self, mock_fetch):
        with self.assertRaisesMessage(CommandError, "not configured server-side"):
            call_command("fetch_gmail")

    @mock.patch("inbox.management.commands.fetch_gmail.scheduler.fetch_gmail_once", side_effect=RuntimeError("boom"))
    def test_fetch_gmail_reports_failure(self, mock_fetch):
        with self.assertRaisesMessage(CommandError, "Gmail fetch failed: boom"):
            call_command("fetch_gmail")


class RunSchedulerCommandTests(TestCase):

    @mock.patch("inbox.management.commands.run_scheduler.time.sleep", side_effect=KeyboardInterrupt)
    @mock.patch("inbox.management.commands.run_scheduler.scheduler.run_fetch_cycle",
                return_value={"gmail": 1, "messenger": None, "errors": []})
    def test_scheduler_loops_until_interrupted(self, mock_cycle, mock_sleep):
        out = StringIO()

        with self.assertRaises(KeyboardInterrupt):
            call_command("run_scheduler", "--interval", "5", stdout=out)

        mock_cycle.assert_called_once()
        mock_sleep.assert_called_once_with(5)
        self.assertIn("gmail: stored 1", out.getvalue())

    @mock.patch("inbox.management.commands.run_scheduler.time.sleep", side_effect=KeyboardInterrupt)
    @mock.patch("inbox.management.commands.run_scheduler.scheduler.run_fetch_cycle",
                return_value={"gmail": None, "messenger": None, "errors": []})
    def test_scheduler_reads_interval_from_env(self, mock_cycle, mock_sleep):
        with mock.patch.dict("inbox.scheduler.os.environ",
                             {scheduler.FETCH_INTERVAL_ENV_VAR: "120"}):
            with self.assertRaises(KeyboardInterrupt):
                call_command("run_scheduler", stdout=StringIO())

        mock_sleep.assert_called_once_with(120)

    def test_scheduler_rejects_non_positive_interval(self):
        with self.assertRaisesMessage(CommandError, "positive"):
            call_command("run_scheduler", "--interval", "0")

    def test_scheduler_rejects_non_integer_env_interval(self):
        with mock.patch.dict("inbox.scheduler.os.environ",
                             {scheduler.FETCH_INTERVAL_ENV_VAR: "soon"}):
            with self.assertRaisesMessage(CommandError, "must be an integer"):
                call_command("run_scheduler")
