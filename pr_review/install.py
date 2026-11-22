"""Install an explicit, content-pinned per-user LaunchAgent on macOS."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import sys
import tempfile

from .core import ReviewError, execute, private_directory, save


def absolute_state(config: dict) -> Path:
    state = Path(config["state_dir"]).expanduser()
    if not state.is_absolute():
        raise ReviewError("Scheduled jobs require an absolute expanded state_dir")
    return state


def plist(config: dict, config_path: Path, release: Path, python: str, home: Path) -> dict:
    label = config.get("label", "com.pr-description-review.weekly")
    if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]+", label):
        raise ReviewError("Invalid LaunchAgent label")
    hour, minute = config.get("hour", 9), config.get("minute", 0)
    if type(hour) is not int or type(minute) is not int or not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ReviewError("Invalid LaunchAgent schedule")
    state = absolute_state(config)
    return {"Label": label, "Comment": "Managed by pr-description-review install",
            "ProgramArguments": [python, "-I", str(release / "run.py"), "--config", str(config_path), "run", "--publish"],
            "WorkingDirectory": str(release), "RunAtLoad": False, "Disabled": True,
            "StartCalendarInterval": {"Weekday": 6, "Hour": hour, "Minute": minute},
            "EnvironmentVariables": {"HOME": str(home), "PATH": str(home / ".local/bin") + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"},
            "StandardOutPath": str(state / "launchd.log"), "StandardErrorPath": str(state / "launchd.error.log"),
            "ExitTimeOut": 60}


def runtime_files(root: Path) -> dict[str, bytes]:
    paths = [root / "run.py", root / "SKILL.md", root / "README.md", root / "LICENSE", root / "references/rubric.md", *sorted((root / "pr_review").glob("*.py"))]
    if any(not p.is_file() or p.is_symlink() for p in paths):
        raise ReviewError("Runtime files must be ordinary source files")
    return {str(p.relative_to(root)): p.read_bytes() for p in paths}


def snapshot(root: Path, releases: Path) -> Path:
    files = runtime_files(root)
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    identity = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    private_directory(releases)
    target = releases / identity
    if target.exists():
        inventory = {str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()}
        if target.is_symlink() or inventory != set(files) | {"runtime-manifest.json"} or any(not (target / name).is_file() or (target / name).is_symlink() or (target / name).read_bytes() != content for name, content in files.items()):
            raise ReviewError("An existing pinned runtime differs from its content identity")
        return target
    temporary = Path(tempfile.mkdtemp(prefix=".runtime-", dir=releases))
    try:
        for name, data in files.items():
            output = temporary / name
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            output.write_bytes(data)
            output.chmod(0o400)
        save(temporary / "runtime-manifest.json", manifest)
        temporary.rename(target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target


def install_agent(config: dict, config_path: Path, *, enable: bool, home: Path | None = None, runner=execute) -> dict:
    if sys.platform != "darwin":
        raise ReviewError("LaunchAgent installation requires macOS")
    if enable and config.get("publish_aggregates") is not True:
        raise ReviewError("Approve aggregate publication before enabling the job")
    home = (home or Path.home()).resolve()
    state = private_directory(absolute_state(config))
    for key in ("github_prefix", "copilot_prefix", "notifier_prefix"):
        executable = config[key][0]
        if not Path(executable).is_absolute() or not Path(executable).is_file() or not os.access(executable, os.X_OK):
            raise ReviewError("Installed command prefixes require absolute executable paths")
    if config.get("timezone", "local") != "local":
        raise ReviewError("Scheduled jobs require timezone=local")
    releases = home / ".local/share/pr-description-review/releases"
    links = [home / root / "pr-description-review" for root in (".agents/skills", ".copilot/skills")]
    for link in links:
        if link.is_symlink():
            if link.resolve().parent != releases:
                raise ReviewError("An existing skill link isn't managed by this installer")
        elif link.exists():
            raise ReviewError("Refusing to replace an existing skill directory")
    root = Path(__file__).resolve().parents[1]
    release = snapshot(root, releases)
    python = str(Path(sys.executable).absolute())
    runner([python, "-I", str(release / "run.py"), "--help"])
    spec = plist(config, config_path, release, python, home)
    spec["Disabled"] = not enable
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    target = agents / (spec["Label"] + ".plist")
    if target.is_symlink():
        raise ReviewError("Refusing a symbolic-link LaunchAgent")
    if target.exists():
        previous = plistlib.loads(target.read_bytes())
        if previous.get("Comment") != spec["Comment"]:
            raise ReviewError("Refusing to replace an unmanaged LaunchAgent")
        save(state / "previous-agent.plist", target.read_text())
    for name in ("launchd.log", "launchd.error.log"):
        log = state / name
        if log.is_symlink():
            raise ReviewError("Refusing a symbolic-link log")
        if not log.exists():
            save(log, "")
        log.chmod(0o600)
    temporary = state / "candidate.plist"
    save(temporary, plistlib.dumps(spec).decode())
    runner(["/usr/bin/plutil", "-lint", str(temporary)])
    inventory = runner(["/bin/launchctl", "list"])
    if re.search(r"^\S+\s+\S+\s+" + re.escape(spec["Label"]) + r"$", inventory, re.M):
        raise ReviewError("Disable and unload the existing job before reinstalling; installation won't stop it")
    save(target, temporary.read_text(), private_parent=False)
    if enable:
        runner(["/bin/launchctl", "enable", f"gui/{os.getuid()}/{spec['Label']}"])
        runner(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(target)])
        runner(["/bin/launchctl", "print", f"gui/{os.getuid()}/{spec['Label']}"])
    else:
        runner(["/bin/launchctl", "disable", f"gui/{os.getuid()}/{spec['Label']}"])
    for link in links:
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            link.unlink()
        link.symlink_to(release, target_is_directory=True)
    return {"plist": str(target), "release": str(release), "enabled": enable,
            "schedule": f"Saturday {config.get('hour', 9):02d}:{config.get('minute', 0):02d} in the Mac's local time zone"}
