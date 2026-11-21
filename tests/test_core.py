from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from pr_review.core import ReviewError, execute, instant, load, save, window


class CalendarTests(unittest.TestCase):
    def test_exact_saturday_boundary_uses_completed_week(self):
        period = window(instant("2026-11-21T17:00:00Z"), ZoneInfo("America/Los_Angeles"))
        self.assertEqual(period["start"], "2026-11-14T17:00:00Z")
        self.assertEqual(period["end"], "2026-11-21T17:00:00Z")

    def test_one_second_before_boundary_keeps_prior_week(self):
        period = window(instant("2026-11-21T16:59:59Z"), ZoneInfo("America/Los_Angeles"))
        self.assertEqual(period["end"], "2026-11-14T17:00:00Z")

    def test_delayed_monday_run_does_not_move_the_window(self):
        zone = ZoneInfo("America/Los_Angeles")
        self.assertEqual(window(instant("2026-11-23T20:00:00Z"), zone), window(instant("2026-11-21T20:00:00Z"), zone))

    def test_fall_transition_is_169_hours(self):
        period = window(instant("2026-11-07T18:00:00Z"), ZoneInfo("America/Los_Angeles"))
        self.assertEqual(period["start"], "2026-10-31T16:00:00Z")
        self.assertEqual(period["end"], "2026-11-07T17:00:00Z")
        self.assertEqual((instant(period["end"]) - instant(period["start"])).total_seconds(), 169 * 3600)

    def test_spring_transition_is_167_hours(self):
        period = window(instant("2026-03-14T18:00:00Z"), ZoneInfo("America/Los_Angeles"))
        self.assertEqual(period["start"], "2026-03-07T17:00:00Z")
        self.assertEqual(period["end"], "2026-03-14T16:00:00Z")

    def test_missing_time_zone_and_bad_schedule_fail(self):
        with self.assertRaises(ReviewError):
            instant("2026-11-21T09:00:00")
        with self.assertRaises(ReviewError):
            window(datetime(2026, 11, 21), ZoneInfo("UTC"))
        with self.assertRaises(ReviewError):
            window(datetime.now(timezone.utc), ZoneInfo("UTC"), 24)


class StateTests(unittest.TestCase):
    def test_atomic_private_replacement_and_symlink_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state/data.json"
            save(path, {"complete": True})
            self.assertEqual(load(path), {"complete": True})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            save(path, {"complete": False})
            self.assertEqual(load(path), {"complete": False})
            target = Path(directory) / "target"
            target.write_text("preserved")
            path.unlink()
            path.symlink_to(target)
            with self.assertRaises(ReviewError):
                save(path, "replacement")
            self.assertEqual(target.read_text(), "preserved")

    def test_subprocess_failure_does_not_echo_source(self):
        import sys
        with self.assertRaises(ReviewError) as raised:
            execute([sys.executable, "-c", "import sys; sys.stderr.write('PRIVATE_SOURCE'); sys.exit(7)"])
        self.assertNotIn("PRIVATE_SOURCE", str(raised.exception))
        self.assertIn("exit 7", str(raised.exception))
