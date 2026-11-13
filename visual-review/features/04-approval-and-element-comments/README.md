# Feature 04: Approval and element comments

- **Case ID**: VR-04
- **Status**: BUILT. OQ-01 = B (one product question), OQ-08 = A (the run waits). Verdicts per element in `review.json`, batch send, `diagrams` on the run detail, VR-06 banner and repo view.
- **Dossier**: D4
- **Depends on**: VR-03 (canvas).
- **Design basis**: [Q6](../../research/synthesis/q6-approval-and-comments.md).

## What it is

The owner gives a verdict per diagram element (OK, Question, Change, Remove from scope). The verdicts feed one product question with 2 to 4 options. Each comment line carries `anchor: {diagram, element}`. The existing JSONL store holds everything.

## In scope

- Verdict controls in the inspector and a "not yet looked at" state.
- The summary count, for example "12 of 14 elements checked".
- A batch send at the end that builds the answer text.
- The question wording and its options.

## Out of scope

- The policy decision (OQ-01) and the time-boxed default (OQ-08). The owner decides both.
- Slack element verdicts. Slack gets the question text only.

## Documents

[purpose.md](purpose.md), [value.md](value.md), [behavior-scenarios.md](behavior-scenarios.md), [testability.md](testability.md), [verification-criteria.md](verification-criteria.md).
