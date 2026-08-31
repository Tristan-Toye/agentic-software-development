# Twipe engineering standards

<!-- Cross-repo source of truth, distilled from Twipe Confluence (DEV space).
Only what a competent engineer would NOT already do by default is written
out; universal practice is a citable row, not a lesson. Full text lives on
the source pages: Architectural guidelines 5301010623 · (Secure) Coding
Practices 2747143472 · TCG-Approved 6170509434 · Code Review Process
2747143522 · Backend Coding Guidelines 5049221136. -->

This file is the whole cross-repo standard. **A repo's own rules file and
its ADRs outrank it** — they know that repo's language, toolchain, and
history. Read those too; on divergence, the repo wins.

## Priority → change-request severity

| Priority | In review |
|---|---|
| critical | blocking CR, always |
| must have | blocking CR unless motivated |
| should have | should-fix CR |
| nice to have | note, not a CR |

## How they apply

- They bind **generation and review symmetrically**: code you write must
  conform; code you review is assessed against them. A violation is a
  material change request, not a style note.
- **Cite the ID** (`TCG 1002`, `TCG 3004`) whenever a rule drives a choice
  or a change request — the rule IS the motivation.
- **Never deviate silently.** Name the rule, name the reason, get
  agreement. Conformance isn't silent either: when a standard forces a
  choice the user might not expect, say which rule drove it, in one line.
- **Contradictions: the user breaks the tie.** Two rules conflicting, or a
  rule conflicting with repo reality — present both with citations, never
  pick a side. The ruling is recorded (an arbitration, or an ADR in the repo).
- **New code: full conformance. Legacy code: pragmatic** — quick wins yes,
  whole-refactor conformance is a scoped decision (a CHG-* finding), not a
  drive-by. Never reformat surrounding code uninvited.
- A recurring justified deviation means a rule needs to change: raise it
  with the user rather than making a habit of it.

## Citable rules

Cite as `TCG <id>`. Universal engineering practice — the severity is the
part worth knowing. † 3004 is ratified internally (2026-07-20, raised from
nice-to-have to hard policy), not on the Confluence TCG page.

| ID | Priority | Rule |
|---|---|---|
| 1000 | should | No magic strings/numbers — constants/enums. OK in logging and trivially local context. |
| 1001 | should | Specific types for specific data: `Guid`/`MailAddress`, not `string`. Never operate on raw/dynamic data. |
| 1002 | **critical** | **Never `SELECT *`** — even when using every column. Name them. |
| 2000 | must | No broad catches. Catch the most specific exception you can handle; let the rest surface. |
| 2002 | must | Every `switch` has a `default`; on a supposedly exhaustive enum it throws. |
| 3000 | should | Small methods, single responsibility, named helpers. |
| 3001 | nice | No double negatives. |
| 3003 | must | Never commit commented-out code. |
| 3004 † | **critical** | **Law of Demeter** — invoke members only on `this`, parameters, objects you create, and your own fields. Never `a.B.C.Do()`. Exempt: fluent/builder APIs and pure data records, where a chain is projection, not navigation. |
| 4000 | should | Features ship with tests. Tests are code. Cover edge cases; each test standalone, self-cleaning, arrange-act-assert. |
| 5000 | **critical** | **Never commit secrets** — history is public. Env vars / secret manager. |

## Security

Challenge every design against the [OWASP Top 10](https://owasp.org/www-project-top-ten/);
apply OWASP secure-coding practice as a matter of course (parameterized
queries, server-side allow-list validation, contextual output encoding,
least privilege, no sensitive data in errors/logs, framework-provided
auth/session/crypto — never hand-rolled).

Twipe-specific, and the part not derivable from OWASP:

- **Aikido (SAST) gates PRs** — introducing a new high/critical
  vulnerability FAILS the pipeline.
- **File paths never come from the client** — index values mapped to a
  pre-defined list.
- Architectural principles (Privacy by Design, Defense in Depth, Default
  Deny, Fail Securely, Zero Trust) are `must have` minimum: **any**
  deviation needs explicit approval.
- Per-feature security requirements:
  https://requirements.whitespots.io/en/export

## C#/backend conventions

- String interpolation over concatenation. Exceptions: long/looped builds
  → `StringBuilder`; logging → **structured message templates**, so
  metadata attaches:
  `_log.Fatal(e, "Uncaught in {MethodName} for {ClassName}", nameof(ValidateUser), nameof(ALOSubscriptionService));`
- `[Trait(nameof(ALOAuthentication), "Unit")]` on tests, for explorer scoping.
- **Never ignore a broken test** — no commenting-out, no skipping. Broken
  logic → fix it. Flawed test → fix or split it. "Untestable" → find
  another way, or remove it deliberately.
- Time is UTC `DateTimeOffset`; business logic takes an **injected clock**
  (`TimeProvider`), never `DateTime.Now`/`UtcNow` inline.
- Separate functional from refactoring changes — refactor as its own
  commit, never interleaved with logic.
