from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pr_review.core import ReviewError
from pr_review.install import install_agent, plist, snapshot

ROOT = Path(__file__).resolve().parents[1]


def config(home):
    return {"state_dir": str(home / "state"), "github_prefix": [sys.executable], "copilot_prefix": [sys.executable],
            "notifier_prefix": [sys.executable], "publish_aggregates": True, "hour": 9, "minute": 0, "timezone": "local"}


class InstallerTests(unittest.TestCase):
    def test_plist_consumer_receives_saturday_nine_and_isolated_immutable_entrypoint(self):
        home = Path("/example/home")
        spec = plist(config(home), home / "config.json", home / "releases/version", "/usr/bin/python3", home)
        consumed = plistlib.loads(plistlib.dumps(spec))
        self.assertEqual(consumed["StartCalendarInterval"], {"Weekday": 6, "Hour": 9, "Minute": 0})
        self.assertFalse(consumed["RunAtLoad"])
        self.assertTrue(consumed["Disabled"])
        self.assertEqual(consumed["ProgramArguments"], ["/usr/bin/python3", "-I", "/example/home/releases/version/run.py", "--config", "/example/home/config.json", "run", "--publish"])
        self.assertEqual(consumed["EnvironmentVariables"]["HOME"], str(home))
        bad = config(home)
        bad["label"] = "../another-job"
        with self.assertRaises(ReviewError):
            plist(bad, home / "config.json", home / "release", "/usr/bin/python3", home)

    def test_snapshot_runs_from_unrelated_directory_and_rejects_mutation_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            release = snapshot(ROOT, path / "releases")
            self.assertEqual(snapshot(ROOT, path / "releases"), release)
            result = subprocess.run([sys.executable, "-I", str(release / "run.py"), "--help"], capture_output=True, text=True, cwd=path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--config", result.stdout)
            source = release / "pr_review/core.py"
            source.chmod(0o600)
            marker = path / "unexpected-side-effect"
            source.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
            result = subprocess.run([sys.executable, "-I", str(release / "run.py"), "--help"], capture_output=True, text=True, cwd=path)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("runtime content mismatch", result.stderr)
            self.assertFalse(marker.exists())

    def test_unlisted_runtime_files_are_rejected_before_application_import(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            release = snapshot(ROOT, path / "releases")
            (release / "pr_review/unlisted.py").write_text("raise RuntimeError('unreviewed module')\n")
            with self.assertRaises(ReviewError):
                snapshot(ROOT, path / "releases")
            result = subprocess.run([sys.executable, "-I", str(release / "run.py"), "--help"], capture_output=True, text=True, cwd=path)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("runtime file inventory mismatch", result.stderr)

    def test_installer_preserves_a_stable_interpreter_link(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            stable = home / "python-stable"
            stable.symlink_to(sys.executable)
            with patch("pr_review.install.sys.platform", "darwin"), patch("pr_review.install.sys.executable", str(stable)):
                result = install_agent(config(home), home / "config.json", enable=False, home=home, runner=lambda argv: "")
            consumed = plistlib.loads(Path(result["plist"]).read_bytes())
            self.assertEqual(consumed["ProgramArguments"][0], str(stable.absolute()))
            self.assertNotEqual(consumed["ProgramArguments"][0], str(stable.resolve()))

    def test_loaded_job_and_unmanaged_skill_links_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            settings = config(home)
            calls = []
            def runner(argv):
                calls.append(argv)
                return "-\t0\tcom.pr-description-review.weekly\n" if argv[:2] == ["/bin/launchctl", "list"] else ""
            with patch("pr_review.install.sys.platform", "darwin"):
                with self.assertRaisesRegex(ReviewError, "unload"):
                    install_agent(settings, home / "config.json", enable=True, home=home, runner=runner)
            self.assertFalse(any("bootstrap" in c or "bootout" in c for c in calls))
            link = home / ".agents/skills/pr-description-review"
            link.parent.mkdir(parents=True)
            preserved = home / "another-skill"
            preserved.mkdir()
            link.symlink_to(preserved)
            calls.clear()
            with patch("pr_review.install.sys.platform", "darwin"):
                with self.assertRaisesRegex(ReviewError, "isn't managed"):
                    install_agent(settings, home / "config.json", enable=True, home=home, runner=runner)
            self.assertEqual(link.resolve(), preserved.resolve())
            self.assertEqual(calls, [])

    def test_unenabled_install_stays_disabled_and_enabled_install_bootstraps_only_once(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                calls = []
                with patch("pr_review.install.sys.platform", "darwin"):
                    result = install_agent(config(home), home / "config.json", enable=enabled, home=home, runner=lambda argv: calls.append(argv) or "")
                consumed = plistlib.loads(Path(result["plist"]).read_bytes())
                self.assertEqual(consumed["Disabled"], not enabled)
                self.assertEqual(sum("bootstrap" in call for call in calls), int(enabled))
                self.assertEqual((home / ".agents/skills/pr-description-review").resolve(), Path(result["release"]))
                self.assertTrue((Path(result["release"]) / "README.md").is_file())
                self.assertEqual((home / "state/launchd.log").stat().st_mode & 0o777, 0o600)

    def test_shared_launchagents_parent_mode_and_symlink_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            shared = home / "shared-agents"
            shared.mkdir(mode=0o751)
            shared.chmod(0o751)
            parent = home / "Library/LaunchAgents"
            parent.parent.mkdir()
            parent.symlink_to(shared, target_is_directory=True)
            with patch("pr_review.install.sys.platform", "darwin"):
                result = install_agent(config(home), home / "config.json", enable=False, home=home, runner=lambda argv: "")
            self.assertTrue(parent.is_symlink())
            self.assertEqual(shared.stat().st_mode & 0o777, 0o751)
            self.assertTrue(Path(result["plist"]).is_file())
            consumed = plistlib.loads(Path(result["plist"]).read_bytes())
            self.assertTrue(consumed["Disabled"])

    @unittest.skipUnless(sys.platform == "darwin", "Native plist validation requires macOS")
    def test_native_plutil_accepts_the_generated_agent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "agent.plist"
            home = Path(directory)
            path.write_bytes(plistlib.dumps(plist(config(home), home / "config.json", home / "release", sys.executable, home)))
            result = subprocess.run(["/usr/bin/plutil", "-lint", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
