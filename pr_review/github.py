"""Read GitHub evidence through gh-axi's documented base64 response contract."""
from __future__ import annotations

import base64
import difflib
import json
from pathlib import Path
import re
import tempfile
from urllib.parse import urlencode

from .core import ReviewError, execute, instant, save

TEMPLATE_PATHS = (".github/pull_request_template.md", ".github/PULL_REQUEST_TEMPLATE.md",
                  "pull_request_template.md", "PULL_REQUEST_TEMPLATE.md",
                  "docs/pull_request_template.md", "docs/PULL_REQUEST_TEMPLATE.md")
TEMPLATE_DIRS = (".github/PULL_REQUEST_TEMPLATE", "PULL_REQUEST_TEMPLATE", "docs/PULL_REQUEST_TEMPLATE")


def decode(output: str) -> object:
    if re.search(r"^\s*truncated:\s*true\s*$", output, re.M):
        raise ReviewError("GitHub response was truncated")
    bodies = re.findall(r"^\s*body:\s*(\S+)\s*$", output, re.M)
    if len(bodies) != 1:
        raise ReviewError("Unexpected gh-axi response envelope")
    try:
        return json.loads(base64.b64decode(bodies[0], validate=True))
    except (ValueError, UnicodeError) as exc:
        raise ReviewError("Invalid GitHub response encoding") from exc


def validate_prefix(prefix: list[str]) -> None:
    packages = [arg for arg in prefix if arg == "gh-axi" or arg.startswith("gh-axi@")]
    if any(not re.fullmatch(r"gh-axi@\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", arg) for arg in packages):
        raise ReviewError("Package-runner gh-axi dependencies require an exact version")
    if any(Path(arg).name == "npx" for arg in prefix) and len(packages) != 1:
        raise ReviewError("Configure an exact gh-axi package version for npx")


class GitHub:
    def __init__(self, prefix: list[str], state: Path, runner=execute):
        validate_prefix(prefix)
        self.prefix, self.state, self.runner = prefix, state, runner

    def api(self, path: str, *, payload: dict | None = None, method: str = "GET") -> object:
        argv = self.prefix + ["api", method, path, "--hostname", "github.com", "--jq", "@base64", "--full"]
        if payload is None:
            return decode(self.runner(argv))
        # Avoid quoting or shell expansion of model, PR, and report content.
        with tempfile.TemporaryDirectory(prefix="request-", dir=self.state) as directory:
            source = Path(directory) / "request.json"
            save(source, payload)
            return decode(self.runner(argv + ["--input", str(source)]))

    def graphql(self, query: str, *, missing_ok: bool = False) -> dict:
        result = self.api("/graphql", payload={"query": query}, method="POST")
        if not isinstance(result, dict):
            raise ReviewError("Invalid GraphQL response")
        if result.get("errors"):
            if missing_ok and all(e.get("type") == "NOT_FOUND" for e in result["errors"]):
                return {"repository": None}
            raise ReviewError("GraphQL evidence is incomplete")
        if not isinstance(result.get("data"), dict):
            raise ReviewError("GraphQL data is missing")
        return result["data"]

    def identity(self) -> str:
        data = self.api("/user")
        login = data.get("login") if isinstance(data, dict) else None
        if not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", login):
            raise ReviewError("Cannot verify the authenticated GitHub account")
        return login

    def search(self, author: str, period: dict) -> list[dict]:
        query = f"is:pr author:{author} created:{period['start']}..{period['end']}"
        items, expected = [], None
        for page in range(1, 11):
            result = self.api("/search/issues?" + urlencode({"q": query, "sort": "created", "order": "asc", "per_page": 100, "page": page}))
            if not isinstance(result, dict) or result.get("incomplete_results") is not False:
                raise ReviewError("PR search did not complete")
            total = result.get("total_count")
            if type(total) is not int or total > 1000 or (expected is not None and total != expected):
                raise ReviewError("PR search changed or exceeded its supported limit")
            expected = total
            batch = result.get("items")
            if not isinstance(batch, list):
                raise ReviewError("PR search items are missing")
            items.extend(batch)
            if len(items) >= expected:
                break
            if not batch:
                raise ReviewError("PR search ended before its reported total")
        if len(items) != expected or len({p["node_id"] for p in items}) != expected:
            raise ReviewError("PR search coverage is incomplete")
        if any(not instant(period["start"]) <= instant(p["created_at"]) <= instant(period["end"]) for p in items):
            raise ReviewError("PR search returned an item outside the selected period")
        return [p for p in items if instant(p["created_at"]) < instant(period["end"])]

    def history(self, node_id: str) -> dict:
        cursor, nodes, expected, first = None, [], None, None
        for _ in range(100):
            after = ",after:" + json.dumps(cursor) if cursor else ""
            query = 'query { node(id:' + json.dumps(node_id) + ''') { ... on PullRequest {
              id number title url body createdAt baseRefOid headRefOid changedFiles includesCreatedEdit
              repository { nameWithOwner isPrivate }
              userContentEdits(first:100''' + after + ''') {
                totalCount pageInfo { hasNextPage endCursor }
                nodes { id editedAt deletedAt diff editor { login } }
              }
            } } }'''
            data = self.graphql(query).get("node")
            if not isinstance(data, dict):
                raise ReviewError("PR details are unavailable")
            history = data["userContentEdits"]
            if expected is not None and (expected != history["totalCount"] or data["body"] != first["body"] or data["headRefOid"] != first["headRefOid"]):
                raise ReviewError("Description changed during history collection")
            first = first or data
            expected = history["totalCount"]
            nodes.extend({**node, "diff": None} if node.get("deletedAt") else node for node in history["nodes"])
            info = history["pageInfo"]
            if not info["hasNextPage"]:
                break
            next_cursor = info["endCursor"]
            if not next_cursor or next_cursor == cursor:
                raise ReviewError("History pagination did not advance")
            cursor = next_cursor
        else:
            raise ReviewError("Description history exceeded its supported limit")
        if len(nodes) != expected or len({n["id"] for n in nodes}) != len(nodes):
            raise ReviewError("Description history coverage is incomplete")
        first["userContentEdits"] = nodes
        return first

    def files(self, pr: dict) -> tuple[list[dict], bool]:
        result = []
        repo = pr["repository"]["nameWithOwner"]
        for page in range(1, 31):
            batch = self.api(f"/repos/{repo}/pulls/{pr['number']}/files?per_page=100&page={page}")
            if not isinstance(batch, list):
                raise ReviewError("Changed-file response is invalid")
            result.extend(batch)
            if len(batch) < 100 or len(result) >= pr["changedFiles"]:
                break
        if len({f["filename"] for f in result}) != len(result):
            raise ReviewError("Changed-file pagination repeated a filename")
        complete = len(result) == pr["changedFiles"]
        if not complete and not (len(result) == 3000 and pr["changedFiles"] > 3000):
            raise ReviewError("Changed-file pagination is incomplete")
        check = self.api(f"/repos/{repo}/pulls/{pr['number']}")
        if check["head"]["sha"] != pr["headRefOid"] or check["base"]["sha"] != pr["baseRefOid"] or (check["body"] or "") != pr["body"]:
            raise ReviewError("PR changed during evidence collection")
        return result, complete

    def templates(self, repo: str, ref: str, *, inherited: bool = False) -> list[dict]:
        owner, name = repo.split("/")
        objects = []
        for i, path in enumerate(TEMPLATE_PATHS + TEMPLATE_DIRS):
            objects.append(f"t{i}:object(expression:{json.dumps(ref + ':' + path)}) {{ ... on Blob {{ text }} ... on Tree {{ entries {{ name path type }} }} }}")
        result = self.graphql('query { repository(owner:' + json.dumps(owner) + ',name:' + json.dumps(name) + ') { ' + ' '.join(objects) + ' } }', missing_ok=inherited)["repository"]
        if result is None:
            return []
        candidates = []
        for i, path in enumerate(TEMPLATE_PATHS + TEMPLATE_DIRS):
            value = result[f"t{i}"]
            if not value:
                continue
            if "text" in value:
                candidates.append({"repository": repo, "ref": ref, "path": path, "text": value["text"], "inherited": inherited})
            else:
                for entry in value["entries"]:
                    if entry["type"] == "blob" and entry["name"].lower().endswith((".md", ".markdown", ".txt")):
                        expression = json.dumps(ref + ":" + entry["path"])
                        blob = self.graphql('query { repository(owner:' + json.dumps(owner) + ',name:' + json.dumps(name) + ') { object(expression:' + expression + ') { ... on Blob { text } } } }')["repository"]["object"]
                        candidates.append({"repository": repo, "ref": ref, "path": entry["path"], "text": blob["text"], "inherited": inherited})
        return candidates

    def inherited_templates(self, owner: str) -> list[dict]:
        data = self.graphql('query { repository(owner:' + json.dumps(owner) + ',name:".github") { isPrivate defaultBranchRef { target { oid } } } }', missing_ok=True)["repository"]
        if not data or data["isPrivate"] or not data["defaultBranchRef"]:
            return []
        ref = data["defaultBranchRef"]["target"]["oid"]
        return self.templates(owner + "/.github", ref, inherited=True)


def readable(text: str) -> str:
    output, fence, comment, ticks = [], None, False, 0
    for line in text.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if not comment and not ticks and marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            output.append(line)
            continue
        if fence or (not comment and line.startswith(("    ", "\t"))):
            output.append(line)
            continue
        visible, position = [], 0
        while position < len(line):
            if comment:
                end = line.find("-->", position)
                if end < 0:
                    break
                comment, position = False, end + 3
            elif line[position] == "`":
                token = re.match(r"`+", line[position:])[0]
                if not ticks:
                    ticks = len(token)
                elif len(token) == ticks:
                    ticks = 0
                visible.append(token)
                position += len(token)
            elif not ticks and line.startswith("<!--", position):
                comment, position = True, position + 4
            else:
                visible.append(line[position])
                position += 1
        output.append("".join(visible))
    return "\n".join(output).strip()


def revisions(pr: dict, period: dict) -> dict:
    nodes = sorted(pr["userContentEdits"], key=lambda n: n["editedAt"])
    created = pr["includesCreatedEdit"]
    if created and not nodes:
        raise ReviewError("Initial history version is missing")
    edits = nodes[1:] if created else nodes
    complete = (created or not nodes) and not any(n.get("deletedAt") or n.get("diff") is None for n in nodes)
    changes = []
    for before, after in zip(nodes, nodes[1:]):
        if before.get("deletedAt") or after.get("deletedAt") or before["diff"] is None or after["diff"] is None:
            continue
        changes.append({"edited_at": after["editedAt"], "hidden_only": before["diff"] != after["diff"] and readable(before["diff"]) == readable(after["diff"]),
                        "delta": "\n".join(difflib.unified_diff(before["diff"].splitlines(), after["diff"].splitlines(), n=1, lineterm=""))})
    return {"lifetime": len(edits), "in_week": sum(instant(period["start"]) <= instant(n["editedAt"]) < instant(period["end"]) for n in edits),
            "complete": complete, "hidden_only": sum(c["hidden_only"] for c in changes), "changes": changes}


def collect(github: GitHub, account: str, period: dict, allow_private: bool) -> dict:
    prs, skipped, cache = [], 0, {}
    for item in github.search(account, period):
        pr = github.history(item["node_id"])
        if pr["repository"]["isPrivate"] and not allow_private:
            skipped += 1
            continue
        repo = pr["repository"]["nameWithOwner"]
        pr["files"], pr["file_list_complete"] = github.files(pr)
        key = (repo, pr["baseRefOid"])
        if key not in cache:
            cache[key] = github.templates(*key)
            if not cache[key]:
                inherited_key = (repo.split("/")[0], "inherited")
                if inherited_key not in cache:
                    cache[inherited_key] = github.inherited_templates(inherited_key[0])
                cache[key] = cache[inherited_key]
        pr["templates"] = cache[key]
        pr["template_status"] = "ambiguous" if len(pr["templates"]) > 1 else "candidate" if pr["templates"] else "fallback"
        pr["revisions"] = revisions(pr, period)
        prs.append(pr)
    return {"account": account, "period": period, "prs": prs, "excluded_private": skipped}
