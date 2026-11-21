# A different model for the test authors: observe first, shadow A/B later

Status: **parked**. Decided 2026-11-18: park (a) and (b) for later. Depends on #420.

## Where we stand

| Host | unit-test-author | integration-test-author | implementer | contract-reviewer |
|---|---|---|---|---|
| Claude Code | haiku | sonnet | sonnet | sonnet |
| opencode | glm-5.3-flash | glm-5.3-flash | glm-5.3-flash | glm-5.3 |

Under Claude Code, unit tests and implementation already come from different models; under opencode
they come from the same one.

## What the evidence says

- Against the hope: separately built versions fail on the same hard inputs. Knight–Leveson (27
  versions, independence rejected), Kim et al. (LLM errors correlated, larger models more so), Ron
  et al. (N-version coding agents: 429 coincident failures against 115 expected, traced to ambiguous
  spec sections). Independence comes mostly from blindness and contract clarity, which the pipeline
  already enforces.
- For it: model diversity helps where models read a docstring differently, and the two hosts
  already give a same-model and a cross-model pipeline to compare.

## (a) Observational comparison, no new runs

Trigger: #420 has run on about 5 builds on each host.

A report over `## Build log` of finished dossiers, per host: mutation kill rate (primary, usage,
floor), `MUTANT-EQUIVALENT` count, row-1 arbitrations (test asserted more than the contract),
property counterexamples outside the domain, `GAP:` count. Confounded (different repos and
dossiers), so it only decides whether (b) is worth running.

## (b) Shadow A/B

Only if (a) shows a real difference, and only after the driver rollout frees the shadow quota
(3, sequential).

On N builds, a second blind unit-test author on another model (e.g. sonnet next to haiku) writes
tests for the same surfaces; they run against the same implementation and never affect the verdict.
Compare the same measures as (a), per surface, on identical inputs. Cost: doubles unit-test-author
spend on those builds.

## Decide

Switch a host's unit-test-author model only if (b) shows a better kill rate without more row-1
rulings.
