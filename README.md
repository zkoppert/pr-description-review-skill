# Review PR descriptions without creating another audit log

PR descriptions can collect implementation history, repeat validation, or lose important facts during shortening. This skill reviews the writing and its revisions with source evidence.

It scores **accuracy**, **brevity**, **ease of understanding**, **template adherence**, and **clarity**. A weekly Mac job can publish aggregate recommendations in a secret gist and send a notification that opens it.

## Understand the boundaries

- Accuracy means alignment with available source, not independent proof of test execution or production measurements.
- Three tool-disabled Copilot reviewers use the same anchors. One explicitly challenges assumptions.
- Grades use the median of at least two known scores. Unknown dimensions remain unknown, and reviewer disagreement stays visible.
- The collector checks actual description histories, including GitHub's initial-version flag. It distinguishes lifetime edits from in-week edits.
- Individual grades, quotations, source identifiers, and revision explanations stay local.
- The gist uses numeric aggregates and fixed recommendations only. It doesn't copy free-form model prose or PR content.
- Secret gists are **unlisted, not access-controlled**. Anyone with the link can read them.

The automated panel provides writing signals, not certified findings or a performance rating. Inspect important source-grounded observations before changing policy.

## Install the requirements

Use Python 3.11 or newer on macOS or Linux. Install [gh-axi](https://github.com/kunchenguid/gh-axi) and the [GitHub Copilot CLI](https://github.com/github/copilot-cli).

The Mac schedule also requires [terminal-notifier](https://github.com/julienXX/terminal-notifier). Authenticate the GitHub and Copilot CLIs before testing.

This repository has no Python package dependencies. Install system tools through your organization's approved package route.

```bash
# Clone the public skill into your normal repository directory.
git clone https://github.com/zkoppert/pr-description-review-skill.git
cd pr-description-review-skill

# Check the portable tests without contacting GitHub or a model.
python3 -m unittest discover -s tests -v
```

## Create a private configuration

Choose three approved non-Gemini model IDs from your current Copilot catalog. The Mac example asks for those IDs and leaves private inference and publication disabled.

```bash
printf 'First approved model ID: '
IFS= read -r PR_REVIEW_MODEL_A
printf 'Second approved model ID: '
IFS= read -r PR_REVIEW_MODEL_B
printf 'Third approved model ID: '
IFS= read -r PR_REVIEW_MODEL_C
export PR_REVIEW_MODEL_A PR_REVIEW_MODEL_B PR_REVIEW_MODEL_C
python3 - <<'PY'
import json
import os
from pathlib import Path
import shutil

home = Path.home()
commands = {name: shutil.which(name) for name in ('npx', 'copilot', 'terminal-notifier')}
if not all(commands.values()):
    raise SystemExit('Install npx, copilot, and terminal-notifier first')
config = {
    'author': '',  # An empty value selects the authenticated account.
    'github_prefix': [commands['npx'], '-y', 'gh-axi@0.1.35'],
    'copilot_prefix': [commands['copilot']],
    'notifier_prefix': [commands['terminal-notifier']],
    'models': [os.environ['PR_REVIEW_MODEL_' + key].strip() for key in ('A', 'B', 'C')],
    'state_dir': str(home / '.local/state/pr-description-review'),
    'timezone': 'local',
    'hour': 9,
    'minute': 0,
    'allow_private': False,
    'private_provider_approved': False,
    'publish_aggregates': False,
}
path = home / '.config/pr-description-review/config.json'
path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
path.write_text(json.dumps(config, indent=2) + '\n')
path.chmod(0o600)
print(path)
PY
```

Confirm that all three model IDs are available through your approved Copilot route. Change the configured IDs when necessary; the job never switches models silently.

A command prefix is an argument array, not a shell string. Organizations can insert their approved package gateway before the package runner.

Use an exact package version or an absolute installed CLI path. The example pins the tested `gh-axi@0.1.35` version. Review CLI updates explicitly rather than resolving an unversioned package during scheduled runs.

For private PRs, obtain approval for the source content and provider. Then set both `allow_private` and `private_provider_approved` to `true`.

Don't approve a provider merely because its CLI works. The configured route must satisfy the source organization's data-handling rules.

An optional `style_checker` can name a Python text-linter file. The job runs it against the generated report before publication.

## Preview the weekly review

```bash
# This creates private local evidence without GitHub writes or notifications.
python3 run.py --config "$HOME/.config/pr-description-review/config.json" run
```

The report covers PRs **created** during the preceding completed Saturday 09:00 to Saturday 09:00 interval. Each boundary uses local calendar time, including daylight-saving changes.

The interval is half-open: it includes the start and excludes the end. Descriptions and histories reflect the reported collection time, which can be later.

The private period directory contains `corpus.json`, `assessment.json`, model receipts, and `report.md`. Assessments include individual scores, source quotations, and explanations of the revisions.

Existing period evidence is reused. Changing model configuration or private-source scope requires inspection rather than silently mixing incompatible receipts.

The preview is a view of canonical aggregate output, not a free-form publication draft. Source fingerprints detect changed evidence. The helper validates cached panels, rebuilds their aggregates, and rejects edited Markdown before publishing. Detailed notes belong in a separate private file.

For a historical preview, use an explicit timestamp with a time zone:

```bash
# Time overrides are preview-only and cannot publish a misleading future period.
python3 run.py --config "$HOME/.config/pr-description-review/config.json" run --at 2026-01-17T18:00:00Z
```

## Publish and verify the report

Inspect the preview and decide whether its aggregate output is appropriate for an unlisted gist. Set `publish_aggregates` to `true` only after that approval.

```bash
# Publication verifies owner, secret visibility, filename, and exact contents.
python3 run.py --config "$HOME/.config/pr-description-review/config.json" run --publish
```

Each period has a deterministic filename and a frozen report. A retry reconciles owned gists before another creation attempt.

An uncertain write stops the current run. If it succeeded remotely, the next run reuses that verified gist instead of creating another.

If no matching gist appears after an attempted creation, the helper stops for explicit resolution. An empty listing doesn't prove that a delayed request cannot finish later. Don't delete the journal or clear the attempt merely to retry.

Conflicting contents, duplicate matching gists, or incomplete reconciliation stop publication. The helper doesn't overwrite a report to hide disagreement.

The notification opens only the verified HTTPS gist URL. A successful command means macOS accepted the request, not that anyone saw or clicked it.

## Schedule Saturday at 09:00

Use the Mac's local time zone for scheduled operation. The installer copies a content-pinned runtime outside the checkout and installs skill-discovery links.

```bash
# Run this only after preview, publication, and notification checks succeed.
python3 run.py --config "$HOME/.config/pr-description-review/config.json" install --enable
```

The per-user LaunchAgent uses `StartCalendarInterval`, with Saturday represented by `Weekday=6`. It doesn't run immediately when loaded.

Inspect the default job:

```bash
# Inspect the loaded schedule without triggering another report.
launchctl print "gui/$(id -u)/com.pr-description-review.weekly"
```

Logs stay in the configured private state directory. A failed publishing run sends a separate local notification and exits unsuccessfully.

Startup failures can occur before notification code runs. Inspect the launchd logs if an expected weekly report doesn't arrive.

Sleep can delay launchd. The job selects a completed calendar interval, reports gaps after the last completed period, and doesn't automatically replay multiple missed weeks.

An unfinished publication retains its selected period. A later publishing run resumes that period before selecting another one.

The snapshot verifies its content manifest before importing the application. This detects accidental runtime drift; it doesn't protect against a malicious process with the same account access.

Updates require reviewing the new source, rerunning tests and a preview, and explicitly reinstalling. Disable and unload an existing job before reinstalling; installation won't stop it automatically. The review application runs from its verified snapshot, not the checkout. External CLIs and an optional style checker remain operator-managed dependencies.

To stop the job:

```bash
# Disable future starts, then unload this specific job.
launchctl disable "gui/$(id -u)/com.pr-description-review.weekly"
launchctl bootout "gui/$(id -u)/com.pr-description-review.weekly"
```

Keep the state directory when troubleshooting. Deleting publication receipts can prevent safe automatic recovery.

## Know the limits

The helper currently supports one authenticated account on github.com. GitHub search supports at most 1,000 selected PRs for one review window.

Incomplete search or pagination stops the review. Documented missing patches and the changed-file API limit reduce source coverage instead of proving a claim false.

Local template candidates use the PR snapshot's recorded base commit, not necessarily its creation base. Inherited candidates use a separately recorded community-default commit collected at review time. Multiple candidates remain explicitly ambiguous.

Reviewer batches contain at most 110,000 serialized source characters. A larger single PR bundle stops before inference and requires local evidence review.

Model output can still contain mistakes despite strict schemas, exact quotations, and panel agreement. Detailed findings remain local for verification.

The job sends approved source evidence to the configured scoring service. The privacy-safe gist renderer doesn't authorize that separate transfer.

Use [the full rubric](references/rubric.md) and [the agent skill](SKILL.md) when running an interactive review.

## Reuse the skill

The project uses the [MIT License](LICENSE).
