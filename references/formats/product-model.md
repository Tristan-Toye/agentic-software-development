# 6. The product model — `docs/product/model.md`

The product model is the repository's one page of product meaning: the nouns
the product uses, the scenarios it serves, and the rules the owner has set.
`/plan` checks every plan against it — the frame (Phase 1), a code-versus-model
contradiction (Phase 2), the contract names (`R-NAMES`, Phase 3) and the
product-coherence review (Phase 7). It is committed with the code, because
every plan reads it and the owner approves every change to it.

## Shape

```markdown
status: confirmed

# Product model

## Concepts

- **Episode** — one published audio item of a show; has one feed entry.
  (source: ADR-0004)
- **Editor** — the person who changes an episode's text before publication.
  (source: OWNER-RULING W-031 2026-10-02)

## Scenarios

### S1 Publish an episode
An editor uploads audio and text; the system transcribes, generates the show
notes and publishes the feed entry. (source: README "Publishing")
- Change: the editor changes the text after generation → the show notes are
  generated again; the transcript is not.
- Retry: the publish step fails → re-run publishes once; the feed has one entry.

## Product rules

- PR-1: an episode is never published without an editor's approval.
  (source: W-031, approved 2026-10-02)
```

- **First line** — `status: draft` or `status: confirmed`. Only the owner moves
  a draft to `confirmed`, on the `/plan` frame card.
- **Concepts** — one bullet per term: `- **term** — meaning`. A term means one
  thing in the whole repository; a contract name maps to one of them or is a
  new concept (`R-NAMES`).
- **Scenarios** — `### S<n> <name>`, a short narrative, then the **change**
  cases (an input changes after work was done) and the **retry** cases (a step
  runs again) with what each one must redo and what it must not. `R-STAGES`
  walks these cases against every stage the plan touches.
- **Product rules** — `- PR-<n>: <rule>`. Only rules the owner approved.
- **Sources** — every concept, scenario and rule ends with `(source: …)`: an
  ADR, a dossier ID, an `OWNER-RULING`, or a repository document. A line with
  no source is an assumption and does not belong in the file.

## Lifecycle

1. **Bootstrap, once per repository.** When the file does not exist, the first
   `/plan` writes a `status: draft` from the ADRs, the README and `docs/`, the
   research notes and the owner's earlier rulings, and puts it on its frame
   card. The owner corrects it or approves it; approved, it becomes
   `status: confirmed`.
2. **Additions only through the final card.** A later plan never rewrites the
   file. It proposes each new product rule (or a new concept or scenario) on
   the `/plan` final card; the rule is added, with the dossier ID as its
   source, only after the owner approves it there.
3. **In committed mode** the file ships in the plan PR; in local mode it is a
   change in the checkout, which the plan's report names.
