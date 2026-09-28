# 5. ASD-STE100 — the subset the validator enforces

Every word in a dossier and an ADR follows a fixed subset of Simplified
Technical English. `scripts/validate_pipeline.py` checks it over prose lines
only — code fences, tables and headings are skipped. Technical Names are
exempt from every rule (R10): anything in backticks, a file name, a
`snake_case`, `camelCase` or `PascalCase` identifier, or an all-caps token
such as `ADR`.

| Rule | Says | Level |
|---|---|---|
| R1 | One term per concept. A banned word fails: write `use` (not utilise, leverage, employ), `start`, `stop`, `make` (not create, generate, produce), `change` (not modify, adjust, revise), `remove`, `check` (not verify, validate, ensure), `find`, `fix` (not resolve), `problem` (not issue, defect), `about`. The full table is `BANNED_TERMS` in the script. | DEFECT |
| R2 | A sentence has at most 25 words; an instruction at most 20. | WARNING |
| R3 | A sentence over 18 words joined by `and` wants a split. | WARNING |
| R4 | Active voice. `is written`, `was made` and the like are flagged. | WARNING |
| R5 | Simple tenses. No `has been`, no `had done`. | WARNING |
| R7 | No noun cluster of four or more words with no function word between them. | WARNING |
| R8 | A sentence never starts with a bare `This is`, `It means`, `Which makes` — name the subject. | DEFECT |
| R9 | A paragraph has at most six sentences. | WARNING |
| R10 | Technical Names are exempt from R1–R9. | — |

There is no R6; the number is unused and no validator message carries it.
Only R1 and R8 fail a run — the rest are heuristics, so read them as
questions, not verdicts.
