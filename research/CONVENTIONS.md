# Research documentation conventions — the visual-review volume

All research documents in this workspace follow these rules. Read this file before you
write or change a research document.

## Language

Write in simplified technical English (ASD-STE100 style):

- Use short sentences. Use no more than 25 words in one sentence.
- Use the active voice.
- Give one instruction or one fact in one sentence.
- Use the same word for the same thing in the full document. Do not use synonyms for variety.
- Use the present tense where possible.
- Do not use idioms, jokes, or vague words ("leverage", "robust", "seamless").

## Locations

- `visual-review/research/papers/` — one document for each paper (software visualisation, architecture recovery, diagram diffs, review UX).
- `visual-review/research/sources/` — documents for tools, libraries and projects.
- `visual-review/research/synthesis/` — SOTA synthesis documents for each design idea.
- `visual-review/sources/` — downloaded PDFs. Kept local and git-ignored (the plugin repo is copied per commit into every install cache); each paper doc keeps the source URL to re-download.
- `visual-review/features/` — feature cases: one folder each with README (feature case), purpose, value, behavior-scenarios, testability, verification-criteria.
- `inspiration-repos/analysis/` — one deep-analysis document for each inspiration repo.
- `research/` (top level) — overview, decision register (`OPEN-QUESTIONS.md`),
  binding principles (`ARCHITECTURE-PRINCIPLES.md`), step handoffs.

## Paper document template (`papers/<slug>.md`)

```markdown
# <Paper title>

- **Source**: <arxiv/venue link>
- **PDF**: `sources/<file>.pdf`
- **Relevant to**: <which the visual-review volume component and which design questions>

## Summary
<5-15 sentences. What the paper does, how, and what the results are.>

## Unique insights and approaches
<Bullet list. Only items that are new relative to common practice. For each item:
the insight, and why it matters for the visual-review volume.>

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
```

## Source document template (`sources/<slug>.md`)

```markdown
# <Source name>

- **Type**: tool | framework | protocol | service | MCP server | article
- **Repo / site**: <link>
- **License / cost**: <license; note if any part is paid>
- **Self-host**: yes / no / partial

## Capabilities
<Comprehensive list of what it can do. Group by area.>

## Development resources
<Links: documentation, API reference, examples, community. One line each on what
the link gives you.>

## Alternatives
<Alternatives found by active search. One line each: name, link, how it differs.>

## Comparison
<Only when several options serve the same purpose. Table: option × criteria.>

## Recommendation
**USE** | **BUILD** | **SKIP** (pick one, bold)
<Reasons. Note the self-host constraint: the core product must not depend on a paid
subscription. If the best option is paid, name the self-host or build path.>
```

## Rules

- Every claim about a tool needs a link or a file path as evidence.
- Every recommendation states USE, BUILD, or SKIP as the first word.
- New papers you download go into the `sources/` folder of the repo they serve.
- New resources you rely on must get an entry in the reading-list index at the
  workspace root (see `research/LINKS.md`, mirrored into the Chrome reading list).
- Cross-link documents with relative paths so agents can navigate.
