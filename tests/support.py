"""Synthetic fixtures shaped like observed GitHub and gh-axi responses."""
import base64
import copy
import json
from pathlib import Path

PERIOD = {"start": "2026-11-14T17:00:00Z", "end": "2026-11-21T17:00:00Z", "timezone": "America/Los_Angeles", "key": "20261121T170000Z"}
BODY = "## Why\n\nA reader needs a clear example.\n\n## What changed\n\nI added the example.\n\n## Testing\n\nThe example parser passed.\n\n## Rollout\n\nNo service deployment."


def envelope(data):
    body = base64.b64encode(json.dumps(data).encode()).decode()
    return "api_response:\n  body: " + body + "\n  truncated: false\n"


def pull_request():
    return {"id": "PR_synthetic", "number": 1, "title": "Add a clear example", "url": "https://github.com/example-owner/example-app/pull/1",
            "body": BODY, "createdAt": "2026-11-16T18:00:00Z", "baseRefOid": "a" * 40, "headRefOid": "b" * 40,
            "changedFiles": 1, "includesCreatedEdit": True, "repository": {"nameWithOwner": "example-owner/example-app", "isPrivate": False},
            "userContentEdits": [
                {"id": "edit-new", "editedAt": "2026-11-17T18:00:00Z", "deletedAt": None, "diff": BODY, "editor": {"login": "example-owner"}},
                {"id": "edit-created", "editedAt": "2026-11-16T18:00:00Z", "deletedAt": None, "diff": BODY.replace("The example parser passed.", "Validation is pending."), "editor": {"login": "example-owner"}}],
            "files": [{"filename": "example.txt", "status": "added", "additions": 1, "deletions": 0, "patch": "@@ -0,0 +1 @@\n+example"}],
            "file_list_complete": True, "templates": [], "template_status": "fallback",
            "revisions": {"lifetime": 1, "in_week": 1, "hidden_only": 0, "complete": True,
                          "changes": [{"edited_at": "2026-11-17T18:00:00Z", "hidden_only": False, "delta": "-Validation is pending.\n+The example parser passed."}]}}


def review(index=1, score=4, category=None):
    issues = [{"category": category, "source": "body", "quote": "The example parser passed.", "note": "The validation scope could be clearer."}] if category else []
    return {"id": index, "scores": {key: score for key in ("accuracy", "brevity", "ease", "template", "clarity")},
            "confidence": "medium", "rationale": "The change is explained with a bounded validation claim.",
            "revision_outcome": "The edit replaced a pending result with a reported passing parser check.",
            "revision_evidence": ["+The example parser passed."], "issues": issues}


def corpus():
    return {"account": "example-owner", "period": copy.deepcopy(PERIOD), "prs": [pull_request()], "excluded_private": 0}


class GistService:
    """A stateful fake of the observed gist API, including uncertain writes."""
    def __init__(self):
        self.gists = {}
        self.creates = 0
        self.lose_response = False
        self.tamper = None

    def api(self, path, *, payload=None, method="GET"):
        if path.startswith("/gists?"):
            return [{"id": key, "files": value["files"]} for key, value in self.gists.items()]
        if path == "/gists" and method == "POST":
            self.creates += 1
            key = "a" * 32
            value = {"id": key, "owner": {"login": "example-owner"}, "html_url": "https://gist.github.com/example-owner/" + key,
                     "public": payload["public"], "files": {name: {"content": f["content"], "truncated": False} for name, f in payload["files"].items()}}
            self.gists[key] = value
            if self.lose_response:
                self.lose_response = False
                from pr_review.core import ReviewError
                raise ReviewError("Synthetic response loss after the remote commit")
            return {"id": key}
        if path.startswith("/gists/"):
            value = copy.deepcopy(self.gists[path.rsplit("/", 1)[1]])
            if self.tamper:
                self.tamper(value)
            return value
        raise AssertionError("Unexpected API operation: " + path)
