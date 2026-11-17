# Behaviour scenarios

**S1. Normal run.**
Given a ready dossier that passed the contract review,
when Phase 3 ends,
then the diagram-author runs, the validator passes, and a `VISUAL-REVIEW:` line is in the build log.

**S2. Missing line.**
Given a build log without a `VISUAL-REVIEW:` line,
when `validate_pipeline.py --pre-fanout` runs,
then it fails and names the missing line.

**S3. Invalid graph.**
Given the agent returns an element with no evidence and no `inferred` mark,
when the validator runs,
then the validator fails with the element ID, and no owner question is created.

**S4. Repair loop.**
Given a failed validation,
when the orchestrator re-spawns the agent with the error list,
then it gets at most 2 repair attempts, and then the step stops and reports.

**S5. Separation.**
Given the contract-review spawn,
when it runs,
then its input holds no diagram file and no dossier.

**S6. Output folder.**
Given a passing run,
when the folder is read,
then it holds `graph.json`, `diff.json`, `elements.json` and a Mermaid export.

**S7. Dashboard field.**
Given a run with a folder,
when the run-detail API is called,
then the JSON has a `diagrams` field with the file list.

**S8. Read-only agent.**
Given the agent runs,
when it tries to write a product file,
then the tool call is refused.
