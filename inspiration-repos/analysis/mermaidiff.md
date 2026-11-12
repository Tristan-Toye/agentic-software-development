# mermaidiff (osmangoninahid/mermaidiff)

- **Repo**: https://github.com/osmangoninahid/mermaidiff (MIT, `inspiration-repos/mermaidiff/LICENSE`)
- **Clone**: `inspiration-repos/mermaidiff/`
- **Relevant to**: Q4 diagram diffs, Q5 sequence diagrams, Q8 LLM accuracy, Q9 review UX

## What it is
A Claude Code and Codex skill. `/mermaidiff` on a staged change, commit, PR or MR produces a "git diff for behavior": a Mermaid sequence diagram with only the changed steps, coloured new, changed or removed, plus proof lines (`file:line`), a payload diff and open questions (`README.md`). The brief opens in the browser; a follow-up chat message updates the open tab (`skill/scripts/view.py`).

## Architecture
- `skill/SKILL.md`: the agent procedure. Step 0 runs `skill/scripts/collect.py` first, which prints mode, diff, claim and callers from `git grep` in about one second, then the output format.
- `skill/format.md`: the strict output contract (sections, limits, evidence tiers).
- `skill/scripts/view.py` and `viewer.html` (141 lines): render the brief in the browser with Mermaid; re-render updates the open tab.
- `eval/`: ten cases, expected results, and scored runs (`eval/SCORE-2026-10-08-short.md`: 10/10 found, 0 false positives).

## Ideas worth borrowing
- **Deterministic collector before the LLM** (`skill/scripts/collect.py`): facts (diff, callers) are gathered by a script, not by the model. Lowers cost and cuts invented facts (Q2, Q8).
- **"No false positives" rule** (`skill/format.md`, "The first rule"): only state what the code proves; unproven items go into a single `Not checked:` line. Good rule for the review agent prompt (Q8).
- **Evidence tier per changed step** (`skill/format.md`, "Rules"): green = seen in a test or trace, blue = backed by code, yellow = inferred. Never upgrade a tier. Reuse as a badge on each diagram element (Q8, Q9).
- **Diff colouring of a sequence diagram** (`README.md`, "What you get"): new, changed, removed steps in one diagram, unchanged steps hidden, maximum 12 steps. This is the simplest working answer to Q4 and Q5.
- **Claim versus code check** (`skill/format.md`, section 1): flags when a commit message or ticket says something the diff does not show. Apply to plan text versus code.
- **Eval harness with expected answers** (`eval/cases.md`, `eval/expected.md`, `eval/RUN.md`): a way to measure diagram accuracy by false positives. Copy the method.
- **Edit by chat updates the open tab** (`README.md` quick start): the owner asks for a change in plain words and the page updates. Matches the dashboard ask-a-question flow (Q9).

## What not to copy
- Mermaid as the only renderer: sequence diagrams only, no abstraction levels, little interactivity (Q1, Q6).
- Python scripts as the runtime: the dashboard is Bun and TypeScript.
- PR-only scope: visual-review runs before code exists, so the "after" side is always `(inferred)` in its terms (see `skill/SKILL.md`, mode "plan").

## License
MIT.
