import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BOUNDARY = ROOT / "tests/fixtures/cli_boundary.py"


class EntryPointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.external = self.home / "external.json"
        self.external.write_text("{}")
        self.config_path = self.home / "config.json"
        self.config = {"author": "example-owner", "github_prefix": [sys.executable, str(BOUNDARY), "github"],
                       "copilot_prefix": [sys.executable, str(BOUNDARY), "copilot"], "notifier_prefix": [sys.executable, str(BOUNDARY), "notifier"],
                       "models": ["model-a", "model-b", "model-c"], "state_dir": str(self.home / "state"),
                       "timezone": "UTC", "hour": 9, "minute": 0, "allow_private": False,
                       "private_provider_approved": False, "publish_aggregates": True}

    def invoke(self, *args):
        self.config_path.write_text(json.dumps(self.config))
        self.config_path.chmod(0o600)
        return subprocess.run([sys.executable, "-I", str(ROOT / "run.py"), "--config", str(self.config_path), "run", *args],
                              cwd=self.home, env={**os.environ, "REVIEW_FIXTURE_STATE": str(self.external)}, capture_output=True, text=True, timeout=30)

    def test_overlapping_publish_stops_before_api_calls_or_gist_creation(self):
        import fcntl
        state = self.home / "state"
        state.mkdir()
        with (state / "operation.lock").open("a+") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.invoke("--publish")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already running", result.stderr)
        external = json.loads(self.external.read_text())
        self.assertNotIn("api_calls", external)
        self.assertNotIn("creates", external)
        self.assertNotIn("model_calls", external)

    def test_preview_scores_real_entrypoint_without_writes_or_notifications(self):
        result = self.invoke("--at", "2026-11-21T18:00:00Z")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        external = json.loads(self.external.read_text())
        self.assertFalse(output["published"])
        self.assertEqual(len(external["model_calls"]), 3)
        self.assertEqual(output["period"]["end"], "2026-11-21T09:00:00Z")
        self.assertFalse(any(path.startswith("/gists") for method, path in external["api_calls"]))
        self.assertNotIn("notifications", external)
        report = Path(output["local_report"]).read_text()
        self.assertIn("4.0/5", report)
        self.assertNotIn("PRIVATE_SOURCE_IDENTIFIER", report)
        saved = json.loads((Path(output["local_report"]).parent / "assessment.json").read_text())
        self.assertEqual(len(saved["results"][0]["reviews"]), 3)
        self.assertEqual(saved["results"][0]["categories"], ["weak_validation"])

    def test_edited_preview_cannot_bypass_aggregate_publication(self):
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        report = Path(json.loads(first.stdout)["local_report"])
        report.write_text(report.read_text() + "\nPRIVATE_NOTE https://example.invalid/internal\n")
        result = self.invoke("--publish")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("canonical aggregate", result.stderr)
        external = json.loads(self.external.read_text())
        self.assertNotIn("creates", external)
        self.assertEqual(len(external["model_calls"]), 3)

    def test_saved_private_sources_cannot_enter_public_only_scoring(self):
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        directory = Path(json.loads(first.stdout)["local_report"]).parent
        source = directory / "corpus.json"
        data = json.loads(source.read_text())
        data["prs"][0]["repository"]["isPrivate"] = True
        source.write_text(json.dumps(data))
        (directory / "assessment.json").unlink()
        (directory / "report.md").unlink()
        result = self.invoke("--publish")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("approved scope", result.stderr)
        external = json.loads(self.external.read_text())
        self.assertEqual(len(external["model_calls"]), 3)
        self.assertNotIn("creates", external)

    def test_edited_source_cannot_reach_reviewers_when_its_visibility_flag_is_unchanged(self):
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        directory = Path(json.loads(first.stdout)["local_report"]).parent
        source = directory / "corpus.json"
        data = json.loads(source.read_text())
        data["prs"][0]["body"] += "\nPRIVATE_LOCAL_CONTEXT"
        source.write_text(json.dumps(data))
        (directory / "assessment.json").unlink()
        (directory / "report.md").unlink()
        result = self.invoke("--publish")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source record changed", result.stderr)
        external = json.loads(self.external.read_text())
        self.assertEqual(len(external["model_calls"]), 3)
        self.assertNotIn("creates", external)

    def test_duplicate_cached_coverage_cannot_omit_a_selected_pr(self):
        self.external.write_text(json.dumps({"two_prs": True}))
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        directory = Path(json.loads(first.stdout)["local_report"]).parent
        path = directory / "assessment.json"
        data = json.loads(path.read_text())
        self.assertEqual([r["id"] for r in data["results"]], [1, 2])
        data["results"][1] = json.loads(json.dumps(data["results"][0]))
        path.write_text(json.dumps(data))
        (directory / "report.md").unlink()
        result = self.invoke("--publish")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PR coverage", result.stderr)
        external = json.loads(self.external.read_text())
        self.assertNotIn("creates", external)
        self.assertEqual(len(external["model_calls"]), 3)

    def test_saved_period_cannot_inject_a_private_filename(self):
        first = self.invoke()
        self.assertEqual(first.returncode, 0, first.stderr)
        directory = Path(json.loads(first.stdout)["local_report"]).parent
        path = directory / "period.json"
        receipt = json.loads(path.read_text())
        receipt["period"]["key"] = "PRIVATE_PROJECT_IDENTIFIER"
        path.write_text(json.dumps(receipt))
        result = self.invoke("--publish")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("validated state key", result.stderr)
        self.assertNotIn("creates", json.loads(self.external.read_text()))

    def test_rendering_gap_context_stays_frozen_after_publication(self):
        state = self.home / "state"
        state.mkdir()
        (state / "index.json").write_text(json.dumps({"account": "example-owner", "last_completed_end": "2026-01-17T09:00:00Z"}))
        first = self.invoke("--publish")
        self.assertEqual(first.returncode, 0, first.stderr)
        report = Path(json.loads(first.stdout)["local_report"])
        self.assertIn("earlier periods weren't reviewed", report.read_text())
        second = self.invoke("--publish")
        self.assertEqual(second.returncode, 0, second.stderr)
        external = json.loads(self.external.read_text())
        self.assertEqual(external["creates"], 1)
        self.assertEqual(len(external["model_calls"]), 3)
        self.assertEqual(len([n for n in external["notifications"] if "-open" in n]), 1)

    def test_notification_retry_reuses_one_verified_gist_and_frozen_assessment(self):
        self.external.write_text(json.dumps({"fail_notification": True}))
        first = self.invoke("--publish")
        self.assertNotEqual(first.returncode, 0)
        self.assertEqual(json.loads(self.external.read_text())["creates"], 1)
        second = self.invoke("--publish")
        self.assertEqual(second.returncode, 0, second.stderr)
        external = json.loads(self.external.read_text())
        self.assertEqual(external["creates"], 1)
        self.assertEqual(len(external["model_calls"]), 3)
        result = json.loads(second.stdout)
        self.assertTrue(result["notification_command_accepted"])
        successful = [a for a in external["notifications"] if "-open" in a]
        self.assertEqual(successful[-1][-1], result["gist"])
        third = self.invoke("--publish")
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertEqual(json.loads(self.external.read_text())["notifications"], external["notifications"])

    def test_uncertain_publication_recovers_through_real_parser_and_entrypoint(self):
        self.external.write_text(json.dumps({"lose_post": True}))
        self.assertNotEqual(self.invoke("--publish").returncode, 0)
        result = self.invoke("--publish")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(self.external.read_text())["creates"], 1)

    def test_older_pending_period_resumes_and_records_the_actual_collection_time(self):
        from datetime import datetime, timezone
        old = {"start": "2026-01-10T09:00:00Z", "end": "2026-01-17T09:00:00Z", "timezone": "UTC", "key": "20260117T090000Z"}
        state = self.home / "state"
        period = state / "periods" / old["key"]
        period.mkdir(parents=True)
        (state / "index.json").write_text(json.dumps({"account": "example-owner", "pending": old["key"]}))
        (period / "period.json").write_text(json.dumps({"account": "example-owner", "period": old, "allow_private": False,
                                                      "models": self.config["models"], "collected_at": "2026-01-17T10:00:00Z"}))
        before = datetime.now(timezone.utc).replace(microsecond=0)
        result = self.invoke("--publish")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["period"], old)
        index = json.loads((state / "index.json").read_text())
        self.assertNotIn("pending", index)
        self.assertEqual(index["last_completed_end"], old["end"])
        captured = json.loads((period / "corpus.json").read_text())["collected_at"]
        self.assertGreaterEqual(datetime.fromisoformat(captured.replace("Z", "+00:00")), before)
        report = (period / "report.md").read_text()
        self.assertIn("collected at " + captured, report)
        self.assertNotIn("collected at 2026-01-17", report)
        self.assertEqual(json.loads(self.external.read_text())["creates"], 1)

    def test_future_and_naive_time_overrides_cannot_poison_period_state(self):
        for timestamp in ("9999-01-01T12:00:00Z", "2026-11-21T09:00:00"):
            with self.subTest(timestamp=timestamp):
                result = self.invoke("--at", timestamp)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(json.loads(self.external.read_text()), {})
        self.assertFalse((self.home / "state/periods").exists())

    def test_bad_model_output_never_publishes_or_notifies_during_preview(self):
        self.external.write_text(json.dumps({"invalid_model": True}))
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        external = json.loads(self.external.read_text())
        self.assertNotIn("gists", external)
        self.assertNotIn("notifications", external)

    def test_invalid_model_output_stops_a_publishing_run_before_any_gist_write(self):
        self.external.write_text(json.dumps({"invalid_model": True}))
        result = self.invoke("--publish")
        self.assertNotEqual(result.returncode, 0)
        external = json.loads(self.external.read_text())
        self.assertNotIn("creates", external)
        self.assertNotIn("gists", external)
        self.assertTrue(external.get("notifications"))
        self.assertFalse(any("-open" in args for args in external["notifications"]))

    def test_private_provider_and_publication_need_explicit_approval(self):
        self.config["allow_private"] = True
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires approval", result.stderr)
        self.assertEqual(json.loads(self.external.read_text()), {})
        self.config["allow_private"] = False
        self.config["publish_aggregates"] = False
        result = self.invoke("--publish")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("creates", json.loads(self.external.read_text()))

    def test_empty_week_skips_models_but_still_has_an_explicit_report(self):
        self.external.write_text(json.dumps({"empty": True}))
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("model_calls", json.loads(self.external.read_text()))
        self.assertIn("No PRs were created", Path(json.loads(result.stdout)["local_report"]).read_text())
