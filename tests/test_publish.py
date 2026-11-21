import copy
from pathlib import Path
import tempfile
import unittest

from pr_review.core import ReviewError
from pr_review.publish import notify, publish, render
from tests.support import GistService, PERIOD, corpus


def assessment():
    return {"models": ["one", "two", "three"], "results": [{"id": 1,
            "scores": {key: 4 for key in ("accuracy", "brevity", "ease", "template", "clarity")},
            "spread": {key: 0 for key in ("accuracy", "brevity", "ease", "template", "clarity")},
            "categories": ["weak_validation"], "reviews": [{"rationale": "PRIVATE MODEL TEXT"}]}]}


class RenderingTests(unittest.TestCase):
    def test_only_aggregate_fields_cross_the_publication_boundary(self):
        data = corpus()
        for field in ("title", "body", "url"):
            data["prs"][0][field] = "PRIVATE-IDENTIFIER https://unapproved.example/exfiltrate"
        data["account"] = "PRIVATE-ACCOUNT"
        data["prs"][0]["repository"]["nameWithOwner"] = "PRIVATE-REPOSITORY"
        report = render(data, assessment(), "2026-11-21T18:00:00Z")
        self.assertNotIn("PRIVATE", report)
        self.assertNotIn("unapproved.example", report)
        self.assertNotIn("https://", report)
        self.assertIn("4.0/5", report)
        self.assertIn("Post-creation revisions observed: 1", report)
        self.assertIn("State what validation passed", report)
        self.assertIn("unlisted, not access-controlled", report)

    def test_recommendations_use_singular_and_plural_counts(self):
        data, result = corpus(), assessment()
        self.assertIn("on 1 description.", render(data, result, "2026-11-21T18:00:00Z"))
        second = copy.deepcopy(data["prs"][0])
        second.update(id="PR_synthetic_two", number=2)
        data["prs"].append(second)
        second_result = copy.deepcopy(result["results"][0])
        second_result["id"] = 2
        result["results"].append(second_result)
        self.assertIn("on 2 descriptions.", render(data, result, "2026-11-21T18:00:00Z"))

    def test_saved_aggregate_records_cannot_supply_free_text_or_invalid_grades(self):
        data = corpus()
        data["excluded_private"] = "PRIVATE_PROJECT_IDENTIFIER"
        with self.assertRaises(ReviewError):
            render(data, assessment(), "2026-11-21T18:00:00Z")
        data = corpus()
        data["prs"][0]["revisions"]["lifetime"] = "PRIVATE_PROJECT_IDENTIFIER"
        with self.assertRaises(ReviewError):
            render(data, assessment(), "2026-11-21T18:00:00Z")
        for value in ("PRIVATE_PROJECT_IDENTIFIER", True, 6, float("nan")):
            with self.subTest(value=value):
                result = assessment()
                result["results"][0]["scores"]["accuracy"] = value
                with self.assertRaises(ReviewError):
                    render(corpus(), result, "2026-11-21T18:00:00Z")

    def test_unknown_categories_and_partial_coverage_do_not_render(self):
        result = assessment()
        result["results"][0]["categories"] = ["PRIVATE-IDENTIFIER"]
        with self.assertRaises(ReviewError):
            render(corpus(), result, "2026-11-21T18:00:00Z")
        with self.assertRaises(ReviewError):
            render(corpus(), {"results": []}, "2026-11-21T18:00:00Z")

    def test_empty_week_has_no_invented_scores_or_recommendations(self):
        data = corpus()
        data["prs"] = []
        text = render(data, {"results": []}, "2026-11-21T18:00:00Z", gap=True)
        self.assertIn("N/A", text)
        self.assertIn("No PRs were created", text)
        self.assertIn("earlier periods weren't reviewed", text)
        self.assertNotIn("4.0/5", text)


class PublicationTests(unittest.TestCase):
    def test_uncertain_remote_creation_is_reconciled_without_a_duplicate(self):
        service = GistService()
        service.lose_response = True
        with tempfile.TemporaryDirectory() as directory:
            journal = Path(directory) / "publication.json"
            with self.assertRaises(ReviewError):
                publish(service, "example-owner", PERIOD, "frozen report", journal)
            self.assertTrue(journal.exists())
            url = publish(service, "example-owner", PERIOD, "frozen report", journal)
            self.assertEqual(service.creates, 1)
            self.assertEqual(url, "https://gist.github.com/example-owner/" + "a" * 32)
            self.assertEqual(publish(service, "example-owner", PERIOD, "frozen report", journal), url)
            self.assertEqual(service.creates, 1)
            with self.assertRaises(ReviewError):
                publish(service, "example-owner", PERIOD, "new model result", journal)

    def test_unresolved_creation_never_reposts_after_an_empty_listing(self):
        for outcome in ("timeout", "missing-id"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                class Unresolved(GistService):
                    def api(self, path, *, payload=None, method="GET"):
                        if path == "/gists" and method == "POST":
                            self.creates += 1
                            if outcome == "timeout":
                                raise ReviewError("Synthetic request remains unresolved remotely")
                            return {}
                        return super().api(path, payload=payload, method=method)
                service = Unresolved()
                journal = Path(directory) / "publication.json"
                with self.assertRaises(ReviewError):
                    publish(service, "example-owner", PERIOD, "frozen", journal)
                with self.assertRaisesRegex(ReviewError, "unresolved"):
                    publish(service, "example-owner", PERIOD, "frozen", journal)
                self.assertEqual(service.creates, 1)

    def test_public_wrong_owner_wrong_url_and_changed_content_fail_readback(self):
        mutators = [lambda g: g.update(public=True), lambda g: g["owner"].update(login="someone-else"),
                    lambda g: g.update(html_url="https://unapproved.example/exfiltrate"),
                    lambda g: next(iter(g["files"].values())).update(content="modified"),
                    lambda g: next(iter(g["files"].values())).update(truncated=True)]
        for mutate in mutators:
            with self.subTest(mutate=mutate):
                service = GistService()
                service.tamper = mutate
                with tempfile.TemporaryDirectory() as directory:
                    with self.assertRaises(ReviewError):
                        publish(service, "example-owner", PERIOD, "report", Path(directory) / "journal.json")

    def test_duplicate_matching_reports_stop_before_another_write(self):
        service = GistService()
        with tempfile.TemporaryDirectory() as directory:
            publish(service, "example-owner", PERIOD, "report", Path(directory) / "first.json")
            service.gists["b" * 32] = copy.deepcopy(next(iter(service.gists.values())))
            with self.assertRaises(ReviewError):
                publish(service, "example-owner", PERIOD, "report", Path(directory) / "second.json")
        self.assertEqual(service.creates, 1)

    def test_notification_uses_singular_for_one_description(self):
        calls = []
        notify(["notifier"], "https://gist.github.com/example-owner/" + "a" * 32, 1, lambda argv: calls.append(argv))
        message = calls[0][calls[0].index("-message") + 1]
        self.assertEqual(message, "1 description reviewed. Open the weekly recommendations.")

    def test_empty_scope_notification_does_not_claim_there_were_no_authored_prs(self):
        calls = []
        notify(["notifier"], "https://gist.github.com/example-owner/" + "a" * 32, 0, lambda argv: calls.append(argv))
        message = calls[0][calls[0].index("-message") + 1]
        self.assertEqual(message, "No PRs in the configured scope. Open the weekly report.")

    def test_notification_uses_an_argument_array_and_verified_https_target(self):
        calls = []
        url = "https://gist.github.com/example-owner/" + "a" * 32
        notify(["notifier"], url, 7, lambda argv: calls.append(argv))
        self.assertEqual(calls[0][-2:], ["-open", url])
        self.assertNotIn("-execute", calls[0])
        for bad in ("file:///private/report", "https://gist.github.com.evil.example/a", "https://gist.github.com/a?leak=private"):
            with self.assertRaises(ReviewError):
                notify(["notifier"], bad, 7, lambda argv: calls.append(argv))
        self.assertEqual(len(calls), 1)
