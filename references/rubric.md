# Grade descriptions consistently

Use the repository's applicable template. Treat multiple candidates as uncertain when the selected template cannot be established.

Where no template is available, compare with Why, What changed, Testing, and Rollout. Don't claim that today's fallback applied historically.

## Use five dimensions

| Dimension | Excellent, 5 | Focused edit, 3 | Incomplete, 1 |
| --- | --- | --- | --- |
| Accuracy | Main claims match available source and evidence. Important limits are explicit. | An important claim is ambiguous, unsupported, or stale. | The core account is misleading or cannot be evaluated. |
| Brevity | The shortest complete explanation for the scope and risk. | Removable detail or repetition distracts from the change. | Mostly irrelevant process detail buries the change, or essential explanation is absent. |
| Ease of understanding | A neighboring teammate can follow the system flow. | Undefined terms or missing connections require domain knowledge. | The reader must reconstruct the explanation. |
| Template adherence | Applicable sections and operational checklists are answered. | A meaningful answer or required section is missing. | Required structure is absent. |
| Clarity | Changed behavior, validation, scope, and material risk are unambiguous. | One important distinction remains unclear. | Competing or missing explanations obscure what ships. |

Use 4 for minor gaps and 2 for substantial revision. Use an unknown score when available evidence cannot support a judgment.

Report confidence separately. A high writing score doesn't certify the code, test execution, production measurements, or legal compliance.

Don't penalize required template text as authored verbosity. Don't penalize a closed or superseded PR merely because it didn't merge.

Transparent limitations can improve clarity. Their presence isn't evidence that the author did poor work.

## Read revisions as evidence

Use actual before/after versions. Don't infer description edits from the PR's general update timestamp.

GitHub's history can include the initial description. Check `includesCreatedEdit` before subtracting the creation version.

Separate lifetime edits from edits inside the reporting window. Include all editors, and don't infer which person or tool initiated an API edit.

Classify an edit by what it accomplished:

- New evidence or status.
- Scope or design changes.
- A factual correction.
- Better readability.
- Template recovery.
- Hidden metadata updates.
- Useful information lost during shortening.

An edit can have multiple purposes. An edit count isn't a mistake count.

A deleted version can make an outcome unavailable. State that limit rather than inventing the missing transition.

## Preserve useful detail

After shortening, check these questions:

1. What concrete problem does the change solve?
2. What behavior changes, and what important behavior stays unchanged?
3. Why is the approach appropriate for this scope?
4. What validation passed, where, and with what important limit?
5. What must happen before deployment, and how can we recover?

Answer the actual template once. An existing validation question can satisfy Testing without another heading.

Use a number when it helps the reader judge impact. Don't add assertion counts or speculative savings to satisfy a quota.

Keep transcripts, temporary workarounds, and pipeline records outside the main explanation. Preserve material compatibility and rollback constraints.

## Interpret weekly scores carefully

The automated panel uses three distinct models and median grades. Two known grades are necessary for a scored dimension.

A two-point spread identifies disagreement, not a confirmed writing defect. Recommendations require matching categories from at least two reviewers.

Every flagged category includes an exact source quotation locally. Revision explanations also require quotations from accessible history. Missing or unsupported interpretations remain unknown.

These checks connect observations to evidence; they don't prove the model's interpretation.

Don't equate model agreement with correctness. Verify important findings before changing writing policy or presenting the review as a performance assessment.
