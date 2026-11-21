"""Synthetic external CLI boundary for end-to-end entry-point tests."""
import base64
from datetime import datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.support import BODY, pull_request, review

state_path = Path(os.environ["REVIEW_FIXTURE_STATE"])
with state_path.with_suffix(".lock").open("a+") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    mode, args = sys.argv[1], sys.argv[2:]
    result, exit_code = None, 0
    if mode == "github":
        method, path = args[1], args[2]
        payload = json.loads(Path(args[args.index("--input") + 1]).read_text()) if "--input" in args else None
        state.setdefault("api_calls", []).append([method, path])
        if path == "/user":
            result = {"login": "example-owner"}
        elif path.startswith("/search/issues?"):
            query = parse_qs(urlsplit(path).query)["q"][0]
            interval = re.search(r"created:(\S+)\.\.(\S+)", query)
            if interval is None:
                raise AssertionError("Use the supported inclusive date-range query contract")
            start = interval[1]
            created = datetime.fromisoformat(start.replace("Z", "+00:00")) + timedelta(hours=2)
            state["created"] = created.isoformat().replace("+00:00", "Z")
            items = [] if state.get("empty") else [{"node_id": "PR_synthetic", "created_at": state["created"]}]
            if items and state.get("two_prs"):
                items.append({"node_id": "PR_synthetic_two", "created_at": state["created"]})
            result = {"total_count": len(items), "incomplete_results": False, "items": items}
        elif path == "/graphql":
            query = payload["query"]
            if "userContentEdits" in query:
                pr = pull_request()
                if '"PR_synthetic_two"' in query:
                    pr.update(id="PR_synthetic_two", number=2, url="https://github.com/example-owner/example-app/pull/2")
                    for edit in pr["userContentEdits"]:
                        edit["id"] += "-two"
                pr["title"] = "PRIVATE_SOURCE_IDENTIFIER"
                pr["createdAt"] = state["created"]
                pr["userContentEdits"][1]["editedAt"] = state["created"]
                pr["userContentEdits"][0]["editedAt"] = (datetime.fromisoformat(state["created"].replace("Z", "+00:00")) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
                pr["userContentEdits"] = {"nodes": pr["userContentEdits"], "totalCount": 2, "pageInfo": {"hasNextPage": False, "endCursor": None}}
                result = {"data": {"node": pr}}
            elif "defaultBranchRef" in query:
                result = {"data": {"repository": None}}
            else:
                result = {"data": {"repository": {f"t{i}": None for i in range(9)}}}
        elif "/files?" in path:
            result = pull_request()["files"]
        elif re.search(r"/pulls/[12]$", path):
            result = {"head": {"sha": "b" * 40}, "base": {"sha": "a" * 40}, "body": BODY}
        elif path.startswith("/gists?"):
            result = list(state.get("gists", {}).values())
        elif path == "/gists" and method == "POST":
            key = "a" * 32
            value = {"id": key, "owner": {"login": "example-owner"}, "html_url": "https://gist.github.com/example-owner/" + key,
                     "public": payload["public"], "files": {name: {"content": f["content"], "truncated": False} for name, f in payload["files"].items()}}
            state.setdefault("gists", {})[key] = value
            state["creates"] = state.get("creates", 0) + 1
            result = {"id": key}
            if state.pop("lose_post", False):
                exit_code = 7
        elif path.startswith("/gists/"):
            result = state["gists"][path.rsplit("/", 1)[1]]
        else:
            raise AssertionError("Unexpected fixture API path: " + path)
    elif mode == "copilot":
        prompt = sys.stdin.read()
        inputs = json.loads(prompt.split("SOURCE DATA JSON:\n", 1)[1])
        model = args[args.index("--model") + 1]
        state.setdefault("model_calls", []).append(model)
        result = {"reviews": [review(item["id"], score=3 if item["id"] == 2 else 4, category="weak_validation") for item in inputs]}
        if state.get("invalid_model"):
            result = "not valid JSON"
    elif mode == "notifier":
        state.setdefault("notifications", []).append(args)
        if "-open" in args and state.pop("fail_notification", False):
            exit_code = 7
        result = ""
    else:
        raise AssertionError(mode)
    state_path.write_text(json.dumps(state))
    if mode == "github":
        encoded = base64.b64encode(json.dumps(result).encode()).decode()
        print("api_response:\n  body: " + encoded + "\n  truncated: false")
    elif mode == "copilot":
        print(json.dumps(result) if isinstance(result, dict) else result)
    sys.exit(exit_code)
