"""Private state, subprocess execution, and calendar periods."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from zoneinfo import ZoneInfo

AXES = ("accuracy", "brevity", "ease", "template", "clarity")


class ReviewError(Exception):
    """An incomplete or unsafe review that must not be published."""


def execute(argv: list[str], *, stdin: str | None = None, timeout: int = 180) -> str:
    try:
        result = subprocess.run(argv, input=stdin, text=True, capture_output=True,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReviewError(f"Command unavailable or timed out: {Path(argv[0]).name}") from exc
    if result.returncode:
        # Provider stderr can contain source text; keep the public error generic.
        raise ReviewError(f"Command failed: {Path(argv[0]).name} (exit {result.returncode})")
    return result.stdout


def private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise ReviewError("State directories must not be symbolic links")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def save(path: Path, value: object, *, private_parent: bool = True) -> None:
    if private_parent:
        private_directory(path.parent)
    elif not path.parent.is_dir():
        raise ReviewError("The destination directory is unavailable")
    if path.is_symlink():
        raise ReviewError("State files must not be symbolic links")
    content = value if isinstance(value, str) else json.dumps(value, indent=2, sort_keys=True)
    fd, name = tempfile.mkstemp(prefix=".review-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def load(path: Path) -> object:
    return json.loads(path.read_text())


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ReviewError("Timestamps must include a time zone")
    return parsed.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def local_zone() -> ZoneInfo:
    if os.environ.get("TZ"):
        return ZoneInfo(os.environ["TZ"])
    target = str(Path("/etc/localtime").resolve())
    if "/zoneinfo/" in target:
        return ZoneInfo(target.split("/zoneinfo/", 1)[1])
    timezone_file = Path("/etc/timezone")
    if timezone_file.is_file():
        return ZoneInfo(timezone_file.read_text().strip())
    raise ReviewError("Cannot determine the local IANA time zone; configure timezone explicitly")


def window(now: datetime, zone: ZoneInfo, hour: int = 9, minute: int = 0) -> dict:
    if now.tzinfo is None or not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ReviewError("Invalid review time")
    local = now.astimezone(zone)
    saturday = local.date() - timedelta(days=(local.weekday() - 5) % 7)
    end = datetime.combine(saturday, time(hour, minute), tzinfo=zone)
    if now < end:
        end -= timedelta(days=7)
    start = datetime.combine(end.date() - timedelta(days=7), time(hour, minute), tzinfo=zone)
    return {"start": iso(start), "end": iso(end), "timezone": zone.key,
            "key": end.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}


def command(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or not value or any(not isinstance(x, str) or not x for x in value):
        raise ReviewError(f"{name} must be a nonempty argument array")
    return value
