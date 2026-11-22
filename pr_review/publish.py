"""Allowlisted aggregate reports, recoverable secret gists, and notifications."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import re
import statistics
from urllib.parse import urlencode, urlparse

from .core import AXES, ReviewError, digest, execute, instant, iso, load, save
from .scoring import RECOMMENDATIONS


def validate_aggregate_inputs(corpus: dict, assessment: dict) -> None:
    if not isinstance(corpus, dict) or not isinstance(corpus.get("prs"), list) or not isinstance(assessment, dict) or not isinstance(assessment.get("results"), list):
        raise ReviewError("Aggregate records are invalid")
    if type(corpus.get("excluded_private")) is not int or corpus["excluded_private"] < 0:
        raise ReviewError("Aggregate counts must be nonnegative integers")
    for pr in corpus["prs"]:
        history = pr.get("revisions") if isinstance(pr, dict) else None
        if not isinstance(history, dict) or any(type(history.get(k)) is not int or history[k] < 0 for k in ("lifetime", "in_week", "hidden_only")) or type(history.get("complete")) is not bool:
            raise ReviewError("Revision aggregate records are invalid")
    for record in assessment["results"]:
        if not isinstance(record, dict):
            raise ReviewError("Score aggregate records are invalid")
        for field, lower, upper in (("scores", 1, 5), ("spread", 0, 4)):
            values = record.get(field)
            if not isinstance(values, dict) or set(values) != set(AXES) or any(v is not None and (type(v) not in (int, float) or not lower <= v <= upper) for v in values.values()):
                raise ReviewError("Aggregate grades must be bounded numbers or unknown")
        if not isinstance(record.get("categories"), list) or any(not isinstance(c, str) or c not in RECOMMENDATIONS for c in record["categories"]):
            raise ReviewError("Unknown report recommendation")


def render(corpus: dict, assessment: dict, collected_at: str, *, gap: bool = False) -> str:
    # Deliberately never interpolate titles, paths, source URLs, model prose,
    # repository names, account names, quotations, or arbitrary category text.
    validate_aggregate_inputs(corpus, assessment)
    start, end = (iso(instant(corpus["period"][k])) for k in ("start", "end"))
    collected = iso(instant(collected_at))
    results, prs = assessment["results"], corpus["prs"]
    if len(results) != len(prs):
        raise ReviewError("Report coverage is incomplete")
    lines = ["# Weekly PR description review", "", f"PR creation period: {start} to {end} (end excluded).",
             f"Descriptions and evidence collected at {collected}.", "",
             f"Descriptions assessed: {len(prs)}.", f"Private PRs excluded by configuration: {corpus['excluded_private']}.", "",
             "Scores are editorial judgments. Accuracy measures alignment with available changes, not independent verification of every claim.",
             "Reported test results and production measurements weren't independently reproduced.", "",
             "## Review the scores", "", "| Dimension | Median-panel mean | Descriptions scored |", "| --- | ---: | ---: |"]
    labels = ("Accuracy", "Brevity", "Ease of understanding", "Template adherence", "Clarity")
    for axis, label in zip(AXES, labels):
        known = [r["scores"][axis] for r in results if r["scores"][axis] is not None]
        score = f"{statistics.mean(known):.1f}/5" if known else "N/A"
        lines.append(f"| {label} | {score} | {len(known)} |")
    disagreement = sum(any(v is not None and v >= 2 for v in r["spread"].values()) for r in results)
    lines += ["", "Three independent models use the same anchors. Each description's score is the median of at least two known grades.",
              f"Descriptions with a two-point or larger reviewer spread: {disagreement}.", "", "## Understand the revisions", "",
              f"Post-creation revisions observed: {sum(p['revisions']['lifetime'] for p in prs)}.",
              f"Post-creation revisions inside the creation period: {sum(p['revisions']['in_week'] for p in prs)}.",
              f"Hidden-comment-only changes observed: {sum(p['revisions']['hidden_only'] for p in prs)}.",
              f"Descriptions with incomplete source history: {sum(not p['revisions']['complete'] for p in prs)}.",
              "Revisions can add evidence, change scope, correct facts, improve readability, or restore a template. An edit isn't automatically a mistake.", "",
              "Individual grades and before/after edit explanations stay in the local report.", "", "## Improve the next descriptions", ""]
    counts = Counter(category for r in results for category in set(r["categories"]))
    if not prs:
        lines.append("No PRs were created in the included scope during this period. There are no grades or writing recommendations.")
    elif not counts:
        lines.append("The panel didn't identify a repeated, source-grounded category. Keep explaining the problem, changed behavior, validation, and material risk.")
    for category, count in sorted(counts.items(), key=lambda p: (-p[1], p[0])):
        if category not in RECOMMENDATIONS:
            raise ReviewError("Unknown report recommendation")
        noun = "description" if count == 1 else "descriptions"
        lines.append(f"- {RECOMMENDATIONS[category]} Panel agreement: at least two reviewers on {count} {noun}.")
    lines += ["", "These are text-review signals, not verified software defects. Small score differences don't establish meaningful differences in writing quality."]
    if gap:
        lines += ["", "One or more earlier periods weren't reviewed. This report doesn't include them."]
    if 0 < len(prs) < 5:
        lines += ["", "This is a small sample. Aggregate scores can describe an individual PR when the period contains only one."]
    lines += ["", "## Keep the evidence private", "", "This secret gist is unlisted, not access-controlled. Anyone with its link can read it.",
              "It contains aggregates and fixed recommendations only. Source identifiers, excerpts, and model-written explanations remain local.",
              "", f"<!-- pr-description-review:v1 period={end} -->", ""]
    return "\n".join(lines)


def verify_gist(github, gist_id: str, account: str, filename: str, content: str) -> str:
    if not re.fullmatch(r"[a-fA-F0-9]{5,64}", gist_id):
        raise ReviewError("Invalid gist identity")
    gist = github.api("/gists/" + gist_id)
    parsed = urlparse(gist.get("html_url", ""))
    expected_path = "/" + account.lower() + "/" + gist_id.lower()
    if gist.get("owner", {}).get("login", "").lower() != account.lower() or gist.get("public") is not False:
        raise ReviewError("Gist owner or visibility verification failed")
    if parsed.scheme != "https" or parsed.netloc != "gist.github.com" or parsed.path.lower() != expected_path or parsed.query or parsed.fragment:
        raise ReviewError("Gist destination verification failed")
    files = gist.get("files", {})
    if set(files) != {filename} or files[filename].get("truncated") is not False or files[filename].get("content") != content:
        raise ReviewError("Gist content verification failed")
    return gist["html_url"]


def publish(github, account: str, period: dict, content: str, journal: Path) -> str:
    filename = "pr-description-review-" + period["key"] + ".md"
    identity = {"account": account, "filename": filename, "sha256": digest(content)}
    previous = load(journal) if journal.exists() else {}
    if journal.exists():
        if any(previous.get(k) != v for k, v in identity.items()):
            raise ReviewError("Frozen publication intent changed; inspect the local journal")
        if previous.get("gist_id"):
            return verify_gist(github, previous["gist_id"], account, filename, content)
    else:
        save(journal, {**identity, "prepared_at": iso(datetime.now(timezone.utc))})
    matches, seen = [], set()
    for page in range(1, 101):
        query = urlencode({"per_page": 100, "page": page, "since": period["start"]})
        batch = github.api("/gists?" + query)
        if not isinstance(batch, list):
            raise ReviewError("Cannot reconcile weekly gist publication")
        for gist in batch:
            if gist["id"] in seen:
                raise ReviewError("Gist listing changed during reconciliation")
            seen.add(gist["id"])
            if filename in gist.get("files", {}):
                matches.append(gist["id"])
        if len(batch) < 100:
            break
    else:
        raise ReviewError("Weekly gist reconciliation is incomplete")
    if len(matches) > 1:
        raise ReviewError("Multiple weekly gists match; resolve them before continuing")
    if matches:
        gist_id = matches[0]
    else:
        if previous.get("post_attempted"):
            raise ReviewError("A prior creation outcome is unresolved; don't create another gist without explicit resolution")
        # An empty listing cannot prove an earlier timed-out POST will not
        # complete later. Persist the attempt before sending any creation.
        save(journal, {**identity, "post_attempted": True})
        result = github.api("/gists", method="POST", payload={"description": "Weekly PR description review", "public": False,
                            "files": {filename: {"content": content}}})
        gist_id = result.get("id", "")
    save(journal, {**identity, "gist_id": gist_id, "post_attempted": bool(previous.get("post_attempted")) or not matches})
    return verify_gist(github, gist_id, account, filename, content)


def notify(prefix: list[str], url: str, count: int, runner=execute) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "gist.github.com" or parsed.query or parsed.fragment:
        raise ReviewError("Refusing an unverified notification destination")
    noun = "description" if count == 1 else "descriptions"
    message = f"{count} {noun} reviewed. Open the weekly recommendations." if count else "No PRs in the configured scope. Open the weekly report."
    runner(prefix + ["-title", "Weekly PR description review", "-message", message,
                     "-group", "pr-description-review", "-open", url])
