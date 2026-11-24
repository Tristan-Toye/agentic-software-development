<!-- /work-on, Phase 9, triggered parts. phase-9.md names each trigger; read the
     one section with ref_section, never the whole file. -->

# /work-on Phase 9 — triggered parts

Parts of Phase 9 that apply only when their case arises. `phase-9.md` names each trigger.

### Re-derive every derived id and count the sync could have moved

**Re-derive every derived id and count the sync could have moved.**
`--finalize-ids` settles the ids *this pipeline* mints — ADRs and LRNs.
It knows nothing about ids and counts the **repository** derives from
position or from a file census: row numbers keyed to file order, scenario
or region counts, pins asserting "N tests exist". A target branch that
added a file shifts every one of them, and the shift is silent — the
numbers still look like numbers. Re-use the `CENSUS:` lines Phase 3
logged before the fan-out: they already name every pin and its owner,
so this is a re-derivation of that same inventory, not a fresh grep.
After the sync, re-run each derivation
over the merged tree, re-gate whatever asserts against it, and only then
run step 2. The failure this prevents costs a whole extra verification
round, because it surfaces as a test asserting a count nobody changed.

Where you find one, log it: a repo whose ids are positional is a repo
where every concurrent branch pays this, and that is `GRADUATION:`
material for the target repo's own rules file (content-addressed ids do
not move).

### Architecture diagrams must match the code: the c3 close gate

**Architecture diagrams must match the code (close gate).** For a Rust
repository with `docs/architecture/c3-components-*.md`, run, after the
sync and still inside `X`:

```bash
python3 "${PLUGIN_ROOT}/scripts/validate_pipeline.py" \
  --c3-gate --base origin/<target>
```

It runs `vr_c3_agree.py` on every crate this branch's diff touched, plus
the generator freshness check (`scripts/module_graph.py check`). A DEFECT
means a touched crate is below 95 percent agreement or its c3 file is
stale: **the session does not close.** Regenerate with
`python3 scripts/module_graph.py regen docs/architecture/c3-components-<crate>.md`,
or repair the parser when the diagram is still wrong, commit, and run the
gate again. Never lower the threshold. A `SKIP` line (no c3 files, no
`cargo-modules`, no touched crate with a file) is a stated skip, not a
pass: copy it into the PR description.

### New diagrams: create the stems the dossier lists

**New diagrams.** For every stem on the dossier's `Diagrams new:` line,
create `docs/architecture/<stem>.json` and its `.md` before the PR opens,
inside `X`, and commit them with the rest. The gate (`--diagram-gate
--dossier`) fails the session when a listed stem has no
`docs/architecture/<stem>.json` on the branch, unless the PR description
carries `Diagram <stem> not added: <why>` (the plan was wrong: say how).
Two sources:

- *A Phase 3 visual review ran.* One call per stem, from the repo root,
  mechanical:
  `python3 "${PLUGIN_ROOT}/scripts/vr_promote.py" --dossier-id <ID> --stem <stem> --repo .`
  It promotes the approved draft `.discovery/visual-review/<ID>/arch/<stem>.json`
  (the "Architecture impact" tab the owner reviewed; written by the Phase 3
  preview for a `seq-*` stem, by the `diagram-author` for `c1-*` and `c4-*`)
  and drops the elements the owner marked `remove` in `review.json`. For a
  seq stem lanes are participants and steps are messages (`seq.to` from the
  step's `to`; arrow from `call_kind`: call `->>`, reply `-->>`, async and
  publish `-)`), the messages renumbered and re-chained after a removal.
  Every evidence anchor is then mapped from `meta.base_sha` (the contract
  commit the views were drawn on) to HEAD: through the line diff when its
  region is unchanged, to the definition of the enclosing function at HEAD
  when the body changed, following renames; a backticked name in the
  anchor's note must agree (a repo with no `base_sha` falls back to the
  name alone and prints `WARN`). It writes `docs/architecture/<stem>.json`,
  the `.md` stub when absent, validates and runs `vr_mermaid.py sync`.
  The owner's approval is tied to the file's content, not its bookkeeping:
  each kept element needs an `ok` in `review.json` (`anchor.diagram`
  `arch:<stem>`) on the current approval key of the draft (the dashboard's
  `graph_hash`: the hash of the file without its pure metadata, that is
  `meta.commit`, `base_sha`, `branch`, `generated_at`, the `path`, `line`,
  `end_line` and `note` of source evidence, and any `path:LINE` code anchor
  written in a `detail` or `text`). When only that metadata moved (the
  commit the diagram was drawn on, a code link re-pointed, a line number),
  the approval carries: never ask the owner again, record
  `approval carried: metadata only (<fields>)` in `## Build log` (the line
  `vr_promote.py --check-approval` prints). A changed step, lane, arrow,
  label or element is a new key and needs approval again. Exit 1 is a stop, each with
  the element named:
  `STALE-ANCHOR` (the code no longer has what the anchor pointed at: fix
  the draft or the code and rerun), `UNRESOLVED-CHANGE` (a `change` or
  `question` on the current hash is open; an `ok` never clears it),
  `STALE-APPROVAL` (the owner's lines are on an older draft: it
  changed since, so the owner approves the current file again) or
  `NOT-APPROVED` (no verdict). A dossier from before the fourth part, with
  a `seq-*` stem and no draft, still promotes from the `new_stem` flow of
  `l1.json` and the owner's L1 verdicts. Then commit and rerun the gate.
- *No visual review* (a repo whose language the visual review does not
  cover, today anything but Rust; Phase 3 logged `VISUAL-REVIEW: skipped — ...`). The orchestrator authors the seq graph
  from the code (`arch_find` on the neighbouring diagrams as models): one
  participant lane per crate, process or container, one message per call
  or event in order (`seq.to` the receiving lane, `arrow` `->>`, `-->>`
  for a reply, `-)` for async), **every message with a `path:line` source
  evidence anchor** it opened; a step it cannot anchor is `inferred` with
  a note, never invented. A new external system (`c1-*`) is a node and
  edges added to the c1 context graph. Then write
  `docs/architecture/<stem>.md` (title, one line saying what flow it
  shows, the marker line
  `<!-- generated from <stem>.json by vr_mermaid.py render; edit the JSON, not this block -->`,
  an empty `mermaid` fence holding `sequenceDiagram`,
  `<!-- end generated -->`), run
  `python3 "${PLUGIN_ROOT}/scripts/vr_graph.py" validate docs/architecture/<stem>.json --root .`
  (it must report `ok: true`) and
  `python3 "${PLUGIN_ROOT}/scripts/vr_mermaid.py" sync docs/architecture`,
  commit, and rerun the gate.

The new diagram is part of the PR like any other diagram change; mention
it in the PR description.

### Post-merge cleanup, and the rule that a cleanup never stashes another run's state

**Post-merge cleanup runs only when the user reports the merge and asks for
it, or when the dashboard's merge detector sees the merge and starts
`/after-merge` (`commands/after-merge.md`)** — state work, not code work, so no fresh worktree. Update the dossiers
first (local mode: settle whatever the merge decides in the front matter;
committed mode: `git pull` brings the `done` dossier in, and a discrepancy
goes through a PR, never a write on the target branch; in both, prune the
merged branch locally if it lingers), **then regenerate the overview HTML
report** with the generator `/overview-dossiers` uses, so the dashboard
reflects the merged state without a full overview pass:

```bash
python3 "${PLUGIN_ROOT}/scripts/generate_open_work.py" \
  --root .discovery
```

 Tell the user the output path (`.discovery/analysis/open-work.html`).
 Regenerate the report; never hand-edit it.

 **When several PRs are open and the user merges one**, the remaining PR
 branches are stale against the new target tip. Offer the refresh ritual
 for each remaining branch (a fresh worktree checked out on it):

 ```bash
 git fetch origin && git merge origin/<target>
 python3 "${PLUGIN_ROOT}/scripts/validate_pipeline.py" \
   --finalize-ids --base origin/<target>
 python3 "${PLUGIN_ROOT}/scripts/validate_pipeline.py"
 git restore docs/adr/index.md
 git commit --no-edit && git push    # only when finalize renumbered something
 ```

 The merge is local, so the union drivers apply; finalize renumbers only
 what the landed branch's numbers displace; the PR button goes green
 again. A conflict on code here is the real signal — resolve it through
 `implementer` and re-run the suite if the blast radius moved, exactly as
 in step 1; a bookkeeping-only refresh re-runs nothing.

 **A cleanup never stashes another run's state.** This checkout is shared:
 a sibling run may hold uncommitted `.discovery/` work here right now. A
 recorded failure: a cleanup stashed "pre-existing local modifications" to
 fast-forward, and a concurrent build's dossier reverted to its seeded
 state — a thousand lines gone mid-run. The rules:

 - Before any fast-forward, pull, or clean-tree operation, run
   `git status` and read it. Foreign `.discovery/` modifications mean
   another run is active: **abort with a diagnostic**, do not stash, do
   not proceed. Tell the user what you saw.
 - Never create a stash that carries another run's files. Never drop a
   stash without `git stash list` plus
   `git show --name-only 'stash@{N}'` — inspect first, drop only your own.
 - Restore foreign-run files verbatim, no merge, no re-type:
   `git restore --source='stash@{N}' -- <path>`.
 - A dossier that "reverts" mid-run is a possible sibling stash. Check
   `git stash list` **first** — one step — before any forensics.
