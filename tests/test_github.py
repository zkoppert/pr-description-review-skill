import copy
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from pr_review.core import ReviewError
from pr_review.github import GitHub, collect, decode, readable, revisions
from tests.support import BODY, PERIOD, envelope, pull_request


class TransportTests(unittest.TestCase):
    def test_real_envelope_parser_rejects_truncation_and_invalid_encoding(self):
        self.assertEqual(decode(envelope({"data": [1, 2]})), {"data": [1, 2]})
        for value in (envelope([]).replace("false", "true"), "body: not-base64!", envelope([]) + envelope([])):
            with self.assertRaises(ReviewError):
                decode(value)

    def test_floating_package_dependencies_fail_before_github_access(self):
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            for package in ("gh-axi", "gh-axi@latest", "gh-axi@^0.1.35"):
                with self.assertRaises(ReviewError):
                    GitHub(["npx", "-y", package], Path(directory), lambda argv: calls.append(argv)).identity()
            pinned = GitHub(["npx", "-y", "gh-axi@0.1.35"], Path(directory), lambda argv: envelope({"login": "example-owner"}))
            self.assertEqual(pinned.identity(), "example-owner")
        self.assertEqual(calls, [])

    def test_payload_is_a_private_json_file_not_a_shell_argument(self):
        observed = []
        def runner(argv):
            path = Path(argv[argv.index("--input") + 1])
            observed.append((json.loads(path.read_text()), path.stat().st_mode & 0o777))
            return envelope({"data": {"safe": True}})
        with tempfile.TemporaryDirectory() as directory:
            github = GitHub(["package-runner", "gh-axi@0.1.35"], Path(directory), runner)
            self.assertEqual(github.graphql('{ viewer { login } }'), {"safe": True})
        self.assertEqual(observed, [({"query": '{ viewer { login } }'}, 0o600)])

    def test_optional_missing_community_repository_does_not_hide_other_graphql_errors(self):
        error = {"type": "NOT_FOUND", "message": "Synthetic missing repository"}
        def runner(argv):
            return envelope({"data": {"repository": None}, "errors": [error]})
        with tempfile.TemporaryDirectory() as directory:
            github = GitHub(["axi"], Path(directory), runner)
            self.assertEqual(github.graphql('{ repository { name } }', missing_ok=True), {"repository": None})
            with self.assertRaises(ReviewError):
                github.graphql('{ repository { name } }')
            error["type"] = "FORBIDDEN"
            with self.assertRaises(ReviewError):
                github.graphql('{ repository { name } }', missing_ok=True)

    def test_search_reads_all_pages_and_rejects_changing_totals(self):
        items = [{"node_id": str(i), "created_at": "2026-11-16T18:00:00Z"} for i in range(101)]
        def runner(argv):
            page = int(parse_qs(urlsplit(argv[argv.index("GET") + 1]).query)["page"][0])
            return envelope({"total_count": 101, "incomplete_results": False, "items": items[:100] if page == 1 else items[100:]})
        with tempfile.TemporaryDirectory() as directory:
            github = GitHub(["axi"], Path(directory), runner)
            self.assertEqual(len(github.search("example-owner", PERIOD)), 101)
            def changing(argv):
                page = int(parse_qs(urlsplit(argv[argv.index("GET") + 1]).query)["page"][0])
                return envelope({"total_count": 101 if page == 1 else 102, "incomplete_results": False, "items": items[:100] if page == 1 else items[100:]})
            github.runner = changing
            with self.assertRaises(ReviewError):
                github.search("example-owner", PERIOD)
            github.runner = lambda argv: envelope({"total_count": 1001, "incomplete_results": False, "items": []})
            with self.assertRaises(ReviewError):
                github.search("example-owner", PERIOD)
            github.runner = lambda argv: envelope({"total_count": 1, "incomplete_results": True, "items": items[:1]})
            with self.assertRaises(ReviewError):
                github.search("example-owner", PERIOD)

    def test_supported_date_range_preserves_the_exclusive_end_boundary(self):
        def runner(argv):
            query = parse_qs(urlsplit(argv[argv.index("GET") + 1]).query)["q"][0]
            if "created:" + PERIOD["start"] + ".." + PERIOD["end"] not in query:
                return envelope({"total_count": 1001, "incomplete_results": False, "items": []})
            return envelope({"total_count": 2, "incomplete_results": False, "items": [
                {"node_id": "inside", "created_at": PERIOD["start"]}, {"node_id": "next-period", "created_at": PERIOD["end"]}]})
        with tempfile.TemporaryDirectory() as directory:
            items = GitHub(["axi"], Path(directory), runner).search("example-owner", PERIOD)
        self.assertEqual([p["node_id"] for p in items], ["inside"])

    def test_history_pages_are_complete_and_detect_body_changes(self):
        original = pull_request()
        calls = []
        class Pages(GitHub):
            change_body = False
            def graphql(self, query, **kwargs):
                calls.append(query)
                data = copy.deepcopy(original)
                second = len(calls) > 1
                if second and self.change_body:
                    data["body"] += "Changed concurrently."
                data["userContentEdits"] = {"totalCount": 2, "nodes": original["userContentEdits"][1:] if second else original["userContentEdits"][:1],
                                           "pageInfo": {"hasNextPage": not second, "endCursor": "next" if not second else None}}
                return {"node": data}
        with tempfile.TemporaryDirectory() as directory:
            result = Pages(["axi"], Path(directory)).history("PR_synthetic")
        self.assertEqual(len(result["userContentEdits"]), 2)
        self.assertEqual(revisions(result, PERIOD)["lifetime"], 1)
        calls.clear()
        with tempfile.TemporaryDirectory() as directory:
            changing = Pages(["axi"], Path(directory))
            changing.change_body = True
            with self.assertRaises(ReviewError):
                changing.history("PR_synthetic")

    def test_retained_deleted_version_text_is_removed_before_persistence(self):
        pr = pull_request()
        pr["userContentEdits"][1].update(deletedAt="2026-11-18T18:00:00Z", diff="REMOVED_VERSION_TEXT")
        class API(GitHub):
            def graphql(self, query, **kwargs):
                value = copy.deepcopy(pr)
                value["userContentEdits"] = {"nodes": value["userContentEdits"], "totalCount": 2, "pageInfo": {"hasNextPage": False, "endCursor": None}}
                return {"node": value}
        result = API(["axi"], Path(".")).history(pr["id"])
        self.assertNotIn("REMOVED_VERSION_TEXT", json.dumps(result))
        self.assertFalse(revisions(result, PERIOD)["complete"])
        self.assertEqual(revisions(result, PERIOD)["changes"], [])

    def test_missing_patches_are_partial_evidence_not_transport_errors(self):
        pr = pull_request()
        class API(GitHub):
            def api(self, path, **kwargs):
                if "/files?" in path:
                    return [{"filename": "image.png", "additions": 0, "deletions": 0}]
                return {"head": {"sha": pr["headRefOid"]}, "base": {"sha": pr["baseRefOid"]}, "body": pr["body"]}
        files, complete = API([], Path(".")).files(pr)
        self.assertTrue(complete)
        self.assertNotIn("patch", files[0])

    def test_empty_rest_body_matches_empty_graphql_body(self):
        pr = pull_request()
        pr["body"] = ""
        class API(GitHub):
            def api(self, path, **kwargs):
                return pr["files"] if "/files?" in path else {"head": {"sha": pr["headRefOid"]}, "base": {"sha": pr["baseRefOid"]}, "body": None}
        self.assertEqual(API([], Path(".")).files(pr)[0], pr["files"])

    def test_changed_head_base_and_premature_file_pagination_fail(self):
        pr = pull_request()
        class API(GitHub):
            head, base = "c" * 40, "a" * 40
            def api(self, path, **kwargs):
                return pr["files"] if "/files?" in path else {"head": {"sha": self.head}, "base": {"sha": self.base}, "body": pr["body"]}
        client = API([], Path("."))
        with self.assertRaises(ReviewError):
            client.files(pr)
        client.head, client.base = "b" * 40, "c" * 40
        with self.assertRaises(ReviewError):
            client.files(pr)
        client.base = "a" * 40
        pr["changedFiles"] = 2
        with self.assertRaises(ReviewError):
            client.files(pr)

    def test_repeated_filenames_cannot_masquerade_as_complete_pagination(self):
        pr = pull_request()
        pr["changedFiles"] = 2
        class API(GitHub):
            def api(self, path, **kwargs):
                return pr["files"] * 2 if "/files?" in path else {"head": {"sha": pr["headRefOid"]}, "base": {"sha": pr["baseRefOid"]}, "body": pr["body"]}
        with self.assertRaisesRegex(ReviewError, "repeated"):
            API([], Path(".")).files(pr)

    def test_documented_changed_file_cap_is_partial_evidence(self):
        pr = pull_request()
        pr["changedFiles"] = 3001
        class API(GitHub):
            def api(self, path, **kwargs):
                if "/files?" in path:
                    page = int(parse_qs(urlsplit(path).query)["page"][0])
                    return [{"filename": f"file-{page}-{i}"} for i in range(100)]
                return {"head": {"sha": pr["headRefOid"]}, "base": {"sha": pr["baseRefOid"]}, "body": pr["body"]}
        files, complete = API([], Path(".")).files(pr)
        self.assertEqual(len(files), 3000)
        self.assertFalse(complete)

    def test_template_blobs_and_directory_candidates_keep_the_pinned_ref(self):
        class API(GitHub):
            def graphql(self, query, **kwargs):
                if "security.md" in query:
                    return {"repository": {"object": {"text": "## Security validation"}}}
                entries = {f"t{i}": None for i in range(9)}
                entries["t0"] = {"text": "## Why\n\n## Testing"}
                entries["t6"] = {"entries": [{"name": "security.md", "path": ".github/PULL_REQUEST_TEMPLATE/security.md", "type": "blob"}]}
                return {"repository": entries}
        candidates = API([], Path(".")).templates("example-owner/example-app", "a" * 40)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[1]["text"], "## Security validation")
        self.assertTrue(all(c["ref"] == "a" * 40 and not c["inherited"] for c in candidates))

    def test_private_community_templates_never_enter_a_public_source_review(self):
        pr = pull_request()
        queries = []
        class API(GitHub):
            def search(self, *args):
                return [{"node_id": pr["id"]}]
            def history(self, node):
                return copy.deepcopy(pr)
            def files(self, value):
                return value["files"], True
            def graphql(self, query, **kwargs):
                queries.append(query)
                if "defaultBranchRef" in query:
                    return {"repository": {"isPrivate": True, "defaultBranchRef": {"target": {"oid": "c" * 40}}}}
                values = {f"t{i}": None for i in range(9)}
                if 'name:".github"' in query:
                    values["t0"] = {"text": "PRIVATE TEMPLATE CONTENT"}
                return {"repository": values}
        result = collect(API(["axi"], Path(".")), "example-owner", PERIOD, False)
        self.assertEqual(result["prs"][0]["templates"], [])
        self.assertEqual(result["prs"][0]["template_status"], "fallback")
        self.assertFalse(any('name:".github"' in q and "object(" in q for q in queries))

    def test_multiple_templates_stay_ambiguous_and_private_sources_are_excluded(self):
        pr = pull_request()
        class API:
            def search(self, *args):
                return [{"node_id": "public"}, {"node_id": "private"}]
            def history(self, node):
                value = copy.deepcopy(pr)
                value["repository"]["isPrivate"] = node == "private"
                return value
            def files(self, value):
                return value["files"], True
            def templates(self, *args):
                return [{"path": "first.md"}, {"path": "second.md"}]
        result = collect(API(), "example-owner", PERIOD, False)
        self.assertEqual(result["excluded_private"], 1)
        self.assertEqual(len(result["prs"]), 1)
        self.assertEqual(result["prs"][0]["template_status"], "ambiguous")


class RevisionTests(unittest.TestCase):
    def test_initial_version_is_not_an_edit_and_week_scope_is_separate(self):
        pr = pull_request()
        pr["userContentEdits"].insert(0, {"id": "later", "editedAt": "2026-11-22T18:00:00Z", "deletedAt": None, "diff": BODY + "\nLater.", "editor": {"login": "another-user"}})
        result = revisions(pr, PERIOD)
        self.assertEqual((result["lifetime"], result["in_week"]), (2, 1))
        self.assertEqual(len(result["changes"]), 2)

    def test_comment_updates_are_hidden_but_code_examples_and_visible_tails_are_not(self):
        self.assertEqual(readable("Body\n<!-- hash: old -->"), "Body")
        self.assertNotEqual(readable("```\n<!-- old -->\n```"), readable("```\n<!-- new -->\n```"))
        self.assertNotEqual(readable("<!-- hidden --> visible A"), readable("<!-- hidden --> visible B"))
        self.assertNotEqual(readable("    <!-- old -->"), readable("    <!-- new -->"))
        self.assertEqual(readable("Summary <!-- old -->"), readable("Summary <!-- new -->"))
        self.assertNotEqual(readable("Summary `<!-- old -->`"), readable("Summary `<!-- new -->`"))
        self.assertEqual(readable("Before <!-- multi\nline --> After"), "Before \n After")
        pr = pull_request()
        pr["userContentEdits"][0]["diff"] = "Body\n<!-- hash: new -->"
        pr["userContentEdits"][1]["diff"] = "Body\n<!-- hash: old -->"
        self.assertEqual(revisions(pr, PERIOD)["hidden_only"], 1)

    def test_deleted_versions_make_outcomes_incomplete_without_inventing_a_delta(self):
        pr = pull_request()
        pr["userContentEdits"][1]["diff"] = None
        pr["userContentEdits"][1]["deletedAt"] = "2026-11-18T18:00:00Z"
        result = revisions(pr, PERIOD)
        self.assertEqual(result["lifetime"], 1)
        self.assertFalse(result["complete"])
        self.assertEqual(result["changes"], [])
        pr["userContentEdits"][1]["diff"] = "REMOVED_VERSION_TEXT"
        self.assertEqual(revisions(pr, PERIOD)["changes"], [])

    def test_missing_initial_version_keeps_edit_counts_but_not_complete_outcomes(self):
        pr = pull_request()
        pr["includesCreatedEdit"] = False
        result = revisions(pr, PERIOD)
        self.assertEqual(result["lifetime"], 2)
        self.assertEqual(result["in_week"], 2)
        self.assertFalse(result["complete"])
        self.assertEqual(len(result["changes"]), 1)

    def test_unedited_description_has_no_creation_subtraction(self):
        pr = pull_request()
        pr.update(includesCreatedEdit=False, userContentEdits=[])
        self.assertEqual(revisions(pr, PERIOD)["lifetime"], 0)
        pr["includesCreatedEdit"] = True
        with self.assertRaises(ReviewError):
            revisions(pr, PERIOD)
