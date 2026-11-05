## The independent checker

One agent closes a loop the orchestrator cannot close alone: the
contract-reviewer derives its own promise checklist from the materialised
stubs, so a thin contract is caught before the fan-out instead of at
arbitration. The other half of that pair — turning `PROMISE_CHECKLIST` into
mutants so a weak oracle is caught after the build — is the orchestrator's
own Phase 6 pass, not a spawn.

### contract-reviewer

Spawned between Phase 3 and Phase 4, on the materialised stubs — never on
the dossier or the orchestrator's checklist.

```
WORKTREE_DIR: /abs/path/repo-W-014
CONTRACT_PATHS: src/flush.py
CRITERIA: |
  <the acceptance criteria, verbatim>
BASE_TEMPLATES: src/pipeline/base.py:40-88   # optional: a base class outside the
                                             # contract a stub extends — its template
                                             # method, what it does before the hook
CHECKLIST_RULES: |
  <the observability checklist from references/formats/dossier.md, pasted verbatim —
   the six categories, the visibility question, the unmeasurable-words rule>
```

`BASE_TEMPLATES` is required in practice whenever a stub extends a base
class that is not part of the contract: the base's template method can decide
an edge case (raise on zero outputs, refuse duplicates) before the subclass
body runs, and a reviewer that sees only the subclass stubs cannot see that.

It returns its own full checklist plus `AMBIGUITY:` and `DEFECT:` lines. The
orchestrator diffs the two checklists: every disagreement is a contract
defect caught pre-fan-out. An `AMBIGUITY:` line carries both readings
verbatim — take the two readings to the user as one question, never pick a
side silently.
