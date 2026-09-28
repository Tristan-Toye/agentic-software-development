# Formats — the files this pipeline keeps

This pipeline has three commands and two file kinds, plus one local scratch
ledger. There is no changelog, no review file, no analysis site, no memory
trace, no state file, no anomaly log, and no template directory.

```
.discovery/                 # local by default; see "Two modes" below
├── dossiers/
│   └── <ID>-<slug>.md      # One work item: its contract, its packages, its
│                           #   criteria, its build log. Front matter carries
│                           #   the machine state. KEPT after the build.
└── deferred-ledger.md      # /deferred's fallback ledger, when no dossier
                            #   covers the changeset. Append-only, one block
                            #   per changeset. Local, never committed.
docs/
└── adr/                    # COMMITTED — ADRs ship with the PR that made them
    ├── index.md            # ID | title — the ONLY file a future agent scans.
    │                       #   Derived. CI commits it on development; branches
    │                       #   never do (see "The index contract").
    └── NNNN-<slug>.md      # Extracted by /work-on after the build.
```

`.discovery/` is local by default and a repository may commit it (below);
`docs/adr/` is always committed. `/plan` writes a dossier. `/work-on` builds
it and extracts the ADRs. `/overview-dossiers` reads dossier front matter and
renders status.

**Both files are kept. They answer different questions.** A dossier answers
"what did we build, and what happened while we built it" — it stays as the build
record, and `/overview-dossiers` mines its `## Build log` for pipeline health signals
long after the work ships. An ADR answers "why is the code like this", which is
the question a future agent asks constantly and cannot answer from a build
record.

The difference is **audience, not lifetime**: a dossier is read by whoever asks
about *this* work item, and only ever by ID — so it stays local by default,
and a team that wants the record shared commits it without changing what it
is for (committed mode, below). An ADR is read
by every future agent, through `docs/adr/index.md`, without knowing it exists —
so it is committed and travels with every clone. That is why the ADR
format is strict about titles and consequences, and why extraction is selective:
the index is a scan surface, and a near-duplicate ADR costs every future reader.

## Two modes for `.discovery/`

One check, run in the checkout the command was started from, at the top of
every command:

```bash
python3 ${PLUGIN_ROOT}/scripts/validate_pipeline.py --mode
```

The definition it applies is `git ls-files -- .discovery`: any tracked file
means `committed`, none means `local`. It adds the two checks the table
relies on — `mode: conflict` (exit 1) when `.gitignore` ignores a new
dossier inside a tracked `.discovery/`, and a warning per local-only path
(below) that `.gitignore` does not cover.

| | `local` (default) | `committed` |
|---|---|---|
| Means | `.discovery/` is gitignored working state on this machine. | At least one file under `.discovery/` is tracked. A dossier is a reviewable change. |
| `/plan` writes | `.discovery/dossiers/` in this checkout; nothing is committed. | The same path inside its own worktree on `plan/<KEY>`, forked from `origin/<base>`; the run ends with a PR (Phase 7). |
| `/work-on` writes | This checkout's dossier, by absolute path. | `X`'s copy only, committed on the build branch before every merge and push, so the build record ships inside the build PR. This checkout's copy changes only when a PR merges. |
| `/overview-dossiers` sees | Every dossier, live. | What has merged; an in-flight dossier lives in `../<repo>-<ID>` or `../<repo>-plan-<KEY>`, and both the chat report and the HTML (`--worktrees`) read it there. |
| `/deferred` appends | To the dossier; git sees nothing. | To the dossier — an uncommitted change on the checked-out branch, named in the report and left to the user. |
| Dossier IDs | Minted per working copy; final. | Provisional until the plan PR merges; `/plan` Phase 7 re-checks against the base tip (`formats/evidence-and-ids.md` §4). |

Three paths stay local in **both** modes: `.discovery/analysis/` (the rendered
overview), `.discovery/pr-draft-*.md` (the drafter's scratch), and
`.discovery/deferred-ledger.md`. In committed mode `/plan` Phase 0 puts the
three patterns in `.gitignore`, and every dossier commit adds files by name.

A repository in two minds — tracked files under `.discovery/` while
`.gitignore` also lists `.discovery/` — is neither mode: a new dossier would
vanish from its PR. Every command stops there and shows the user the line.
Modes never mix inside one run.

## Index

| File | Covers |
|---|---|
| `README.md` (this file) | The two file kinds, local vs. committed `.discovery/` |
| `dossier.md` | § 1 — the dossier: front matter, body, contract, work packages, acceptance criteria, build log |
| `adr.md` | § 2 — the ADR: front matter, body, the index contract |
| `evidence-and-ids.md` | § 3 — evidence labels; § 4 — ID minting |
| `writing.md` | § 5 — ASD-STE100, the validator's prose subset |

A section reference elsewhere in this repo now names the file that carries
that section — e.g. `formats/dossier.md` §1, `formats/evidence-and-ids.md`
§4, or `formats/dossier.md` § "The observability checklist".

---
