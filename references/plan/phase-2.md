<!-- /plan, Phase 2. Read at the start of this phase; the rules that hold
     in every phase stay in primary-agents/plan.md. -->

## Phase 2 — Investigate: scouts search, you judge


You are the strongest model in the pipeline and the most expensive one per
token, so spend your context on judgement, not on searching. **Send the
lookups to `code-scout`** (the small model): where a symbol is defined, who
calls it, which files hold a pattern, what a member promises, which tests
name a thing. Ask several scouts at once, each with a few numbered questions
(`ROOT`, `QUESTIONS`, `LIMIT`), in one message, and take their `path:line`
rows back. Then open **only** the anchors you will cite or must reason over,
in ranges. One recorded run of six plans made 907 search calls itself and
spawned no scout; most of its cost was those results written into its own
context. Read, grep and run commands yourself only for the one or two
questions a scout cannot answer (a reproduction, an experiment, history).

**Anchors in one call, never in windows.** The recorded orchestrator sessions
spent their largest share of context on the same source file read in three,
four, five `Read` windows, on `grep -n` without `| head`, and on documents read
twice. Take a file's map with `code_outline`, then every item you will cite in
one `code_item` call (`A | B::c`) and every heading in one `doc_section` call
(`a | b`); a dossier only by `dossier_edit.py section`. The read guard refuses a
4th window on one source file and a window over 200 lines on `.rs`, `.py`,
`.cs` or `.md` files; repeating the identical call overrides it.

**Keep the findings on disk as you go**, in
`${RUN_ROOT}/.agent-staging/plan-<key>/findings.md` (`<key>` is the dossier id,
or the source id and item, `W-128-D-3`): one line per settled finding with its
label and anchor (`FACT src/x.rs:41 — …`, `UNKNOWN — …, answered by …`), and
the open questions. A restarted session (a usage limit, a network error, the
runner's next stage) reads that file and carries on from it instead of
searching again.

Work in `${RUN_ROOT}`: open, grep and blame there, so every anchor you write
is true at the commit Phase 8 stamps. In committed mode that tree is the
remote's tip, which may be ahead of the user's checkout — a difference worth
one line in the report, never a reason to read the older tree.

### The scope of the investigation

Investigate the frame's code **plus one hop** (its direct callers and
callees), the ADRs, and the dossiers in flight that the overlap search found —
nothing wider unless a finding sends you there, and then log why
(`SCOPE-WIDENED: <path> — <finding>`).

Read **signatures and documentation first**: `code_outline` and `code_item`
give a member's signature and docstring. Open a body only to anchor a
behaviour you will state as `FACT`, and log the anchor with the claim. A
behaviour claim that rests on a docstring or a name alone is an `INFERENCE`,
never a `FACT`.

### The spike runs first, in parallel

When the approved frame names a spike, start it at the beginning of this phase
in a throwaway `git worktree` (remove it after), and send the independent
scout questions at the same time. A spike result that disproves something the
outcome depends on (the approach, go or no-go) sends you back to the Phase 1
frame card with the new frame. A result that changes a detail only becomes a
row in Phase 3. Log `SPIKE: <question> — <result>` in the findings file.

### Steps

1. **Check the recorded knowledge first.** Read `docs/adr/index.md` and open
   only the ADRs whose titles are relevant. A decision already recorded is a
   constraint you must respect. A contradiction between the code and the
   product model (`docs/product/model.md`) is not yours to settle: it becomes
   a CONFIRM row in Phase 3.
2. **The pin scan.** For every file the plan will touch or create:

   ```bash
   python3 ${PLUGIN_ROOT}/scripts/plan_flow.py pins --root ${RUN_ROOT} --files <paths>
   ```

   It lists every image tag or digest, every version pin (language, runtime,
   database, library) and every numeric limit (sizes, counts, TTLs, caps),
   each with the ADR that records it or `adr: none`. Copy the `adr: none`
   lines into the findings file: they feed the R-PINS and R-BOUNDS rows.
3. **Learn the shape from the diagrams first.** When the target repo has
   `docs/architecture/`, call `arch_find` on each file path and symbol the
   change will touch before reading code: it returns the containers, classes
   and call order around them (with their source anchors) at a fraction of the
   cost of the `.md` or `.json` files, and `arch_find` with a `diagram` and an
   empty `query` outlines one diagram. Note which diagrams the change would
   alter: Phase 4 records them as the `Diagrams affected:` line. Note too
   whether the change introduces a flow that crosses crates, processes or
   containers (or a new external system) that no existing `seq-*` diagram
   shows: Phase 4 records a proposed diagram for it as the `Diagrams new:`
   line. Searching the existing sequence diagrams for the flow is how you
   tell "already shown" from "new".
4. **Trace the code.** Find where the problem lives. Follow the real call path;
   never assume a function does what its name says. Every claim you will write
   as `FACT` needs a `path:line` anchor you actually opened.
5. **Use history when history answers the question.** `git log`, `git blame`,
   `git show` are a command away. A regression window or a churn hotspot is
   evidence.
6. **Run an experiment when you need one.** For a suspected bug, reproduce it.
   For an empirical unknown, run a timeboxed experiment in a throwaway
   `git worktree`, then remove it. Ask before you run anything that writes
   outside the repository.
7. **Record what you cannot settle** as `UNKNOWN` with the name of who or what
   answers it. Never launder an unknown into an inference.

