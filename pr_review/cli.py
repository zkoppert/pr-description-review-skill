"""Run a private review; publish and notify only when explicitly authorized."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo

from .core import ReviewError, command, digest, execute, instant, iso, load, local_zone, private_directory, save, window
from .github import GitHub, collect, validate_prefix
from .publish import notify, publish, render
from .scoring import assess, validate_assessment


def settings(path: Path) -> dict:
    data = load(path)
    if not isinstance(data, dict):
        raise ReviewError("Configuration must be a JSON object")
    for key in ("github_prefix", "copilot_prefix", "notifier_prefix"):
        command(data.get(key), key)
    validate_prefix(data["github_prefix"])
    if data.get("allow_private") is True and data.get("private_provider_approved") is not True:
        raise ReviewError("Private-source scoring requires approval for the configured Copilot provider")
    if not isinstance(data.get("models"), list):
        raise ReviewError("Configure the three scoring models")
    return data


def run(config: dict, *, publish_report: bool, at: datetime | None = None,
        github_factory=GitHub, assessor=assess, notifier=notify) -> dict:
    if publish_report and config.get("publish_aggregates") is not True:
        raise ReviewError("Aggregate publication requires explicit configuration approval")
    if publish_report and at is not None:
        raise ReviewError("Historical time overrides are preview-only")
    if at is not None and at > datetime.now(timezone.utc):
        raise ReviewError("Future review periods cannot be cached")
    state = private_directory(Path(config["state_dir"]).expanduser())
    lock_path = state / "operation.lock"
    if lock_path.is_symlink():
        raise ReviewError("Refusing a symbolic-link lock")
    with lock_path.open("a+") as lock:
        lock_path.chmod(0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ReviewError("Another review is already running") from exc
        now = at or datetime.now(timezone.utc)
        github = github_factory(config["github_prefix"], state)
        account = github.identity()
        if config.get("author") and config["author"].lower() != account.lower():
            raise ReviewError("Authenticated account differs from the configured author")
        zone = local_zone() if config.get("timezone", "local") == "local" else ZoneInfo(config["timezone"])
        selected = window(now, zone, config.get("hour", 9), config.get("minute", 0))
        index_path = state / "index.json"
        index = load(index_path) if index_path.exists() else {}
        if index.get("account", account).lower() != account.lower():
            raise ReviewError("Review state belongs to another account")
        pending = index.get("pending") if publish_report else None
        key = pending or selected["key"]
        if not isinstance(key, str) or not re.fullmatch(r"\d{8}T\d{6}Z", key):
            raise ReviewError("Invalid saved review period")
        directory = private_directory(state / "periods" / key)
        receipt_path = directory / "period.json"
        if receipt_path.exists():
            receipt = load(receipt_path)
            if receipt["account"].lower() != account.lower():
                raise ReviewError("Saved review account changed")
            selected = receipt["period"]
        else:
            receipt = {"account": account, "period": selected,
                       "allow_private": config.get("allow_private") is True, "models": config["models"]}
            save(receipt_path, receipt)
        if selected.get("key") != key:
            raise ReviewError("Saved period identity differs from its validated state key")
        if receipt["allow_private"] != (config.get("allow_private") is True) or receipt["models"] != config["models"]:
            raise ReviewError("Saved evidence scope or scoring models changed; inspect the local period first")
        if publish_report:
            save(index_path, {**index, "account": account, "pending": key})
        corpus_path, assessment_path, report_path = (directory / name for name in ("corpus.json", "assessment.json", "report.md"))
        if report_path.exists() and (not corpus_path.exists() or not assessment_path.exists()):
            raise ReviewError("Cached report lacks its source records; inspect the local period")
        if corpus_path.exists():
            corpus = load(corpus_path)
        else:
            corpus = collect(github, account, selected, config.get("allow_private") is True)
            corpus["collected_at"] = iso(datetime.now(timezone.utc))
            corpus["source_sha256"] = digest(json.dumps(corpus, sort_keys=True))
            save(corpus_path, corpus)
        if corpus.get("account") != account or corpus.get("period") != selected or not isinstance(corpus.get("prs"), list):
            raise ReviewError("Saved source identity or period changed")
        for pr in corpus["prs"]:
            private = pr["repository"]["isPrivate"]
            if type(private) is not bool or (private and config.get("allow_private") is not True):
                raise ReviewError("Saved source includes private or unknown visibility outside the approved scope")
        source_material = {k: v for k, v in corpus.items() if k != "source_sha256"}
        if corpus.get("source_sha256") != digest(json.dumps(source_material, sort_keys=True)):
            raise ReviewError("Saved source record changed; don't send edited cache data to reviewers")
        if not corpus.get("collected_at"):
            raise ReviewError("Saved corpus lacks a collection timestamp; inspect it before publishing")
        if assessment_path.exists():
            assessment = load(assessment_path)
        else:
            assessment = assessor(corpus, config["copilot_prefix"], config["models"], directory)
            save(assessment_path, assessment)
        assessment = validate_assessment(corpus, config["models"], assessment)
        if "render_gap" not in receipt:
            if report_path.exists():
                raise ReviewError("Cached report lacks its rendering context; inspect it before publishing")
            receipt["render_gap"] = bool(index.get("last_completed_end") and instant(selected["start"]) > instant(index["last_completed_end"]))
            save(receipt_path, receipt)
        if type(receipt["render_gap"]) is not bool:
            raise ReviewError("Saved rendering context is invalid")
        content = render(corpus, assessment, corpus["collected_at"], gap=receipt["render_gap"])
        if report_path.exists() and report_path.read_text() != content:
            raise ReviewError("Cached report differs from canonical aggregate output; edited text cannot be published")
        if not report_path.exists():
            save(report_path, content)
        if config.get("style_checker"):
            execute([sys.executable, config["style_checker"], str(report_path)])
        result = {"period": selected, "local_report": str(report_path), "published": False}
        if not publish_report:
            return result
        url = publish(github, account, selected, content, directory / "publication.json")
        notification = directory / "notification.json"
        if not notification.exists():
            corpus = load(corpus_path)
            notifier(config["notifier_prefix"], url, len(corpus["prs"]))
            save(notification, {"url": url, "report_sha256": digest(content), "command_accepted_at": iso(datetime.now(timezone.utc))})
        save(index_path, {"account": account, "last_completed_end": selected["end"]})
        return {**result, "published": True, "gist": url, "notification_command_accepted": True}


def parse_timestamp(value: str) -> datetime:
    try:
        return instant(value)
    except (ReviewError, ValueError) as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    sub = parser.add_subparsers(dest="action", required=True)
    review = sub.add_parser("run", help="Collect and preview; publication is opt-in")
    review.add_argument("--publish", action="store_true")
    review.add_argument("--at", type=parse_timestamp, help="Preview a historical completed week")
    install = sub.add_parser("install", help="Install a pinned macOS LaunchAgent")
    install.add_argument("--enable", action="store_true")
    args = parser.parse_args(argv)
    try:
        config = settings(args.config.expanduser())
        if args.action == "install":
            from .install import install_agent
            result = install_agent(config, args.config.expanduser().resolve(), enable=args.enable)
        else:
            result = run(config, publish_report=args.publish, at=args.at)
        print(json.dumps(result, indent=2))
        return 0
    except (ReviewError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"pr-description-review: {type(exc).__name__}: {exc}", file=sys.stderr)
        if "config" in locals() and args.action == "run" and args.publish:
            try:
                execute(config["notifier_prefix"] + ["-title", "PR description review needs attention", "-message",
                        "The weekly review didn't finish. Check its private local log.", "-group", "pr-description-review-error"])
            except ReviewError:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
