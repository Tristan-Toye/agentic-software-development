# OpenArc (hvess/openarc)

- **Repo**: https://github.com/hvess/openarc (MIT, `inspiration-repos/openarc/LICENSE`)
- **Clone**: `inspiration-repos/openarc/` (last commit 2026-11-08, version 0.2.0, early design)
- **Relevant to**: the whole visual-review capability; Q1, Q2, Q4, Q6, Q8, Q9

## What it is
OpenArc turns one code change into one self-contained interactive HTML page. The page shows the components the change adds, changes or removes, the named flows through them, pseudocode ("Klar") per function, and risk notes. It is a Claude Code and Codex plugin (`inspiration-repos/openarc/.claude-plugin/plugin.json`, `skills/openarc/SKILL.md`). It is the closest public match to the visual-review goal: a diagram of a planned or finished change, with a diff view, produced by an agent and checked by a script.

## Architecture
1. `bin/openarc.ts new <name>` creates `.openarc/<name>/` with `files.txt`, `change.diff`, `before/` (base versions of changed files), and empty `graph.src.json` and `change.klar` (`skills/openarc/SKILL.md`, step 2).
2. The agent writes `graph.src.json` (structure) and `change.klar` (meaning). The format is plain JSON with a JSON Schema: `spec/graph.md`, `spec/graph.schema.json`.
3. `tools/build.ts` validates the graph (schema, unknown ids, line ranges outside files, missing Klar), copies source `code` from the real files, runs ELK layout at build time (`tools/layout.ts`), and bundles one HTML page (`tools/bundle.ts`, `tools/page.ts`).
4. The viewer is a TypeScript DOM app with no framework: `viewer/src/main.ts`, `cards.ts`, `edges.ts`, `painter.ts`, `inspector.ts`, `sidebar.ts`, `place.ts`.
5. Design tokens drive the page: `design/tokens/*.json`, `tools/tokens.ts`, `tools/contrast.ts` (contrast check), `tools/lint-design.ts`.

## Ideas worth borrowing
- **Change graph schema with status** (`spec/graph.md` section 3): every component, member and edge has `added | changed | removed | context`. A component's status is the summary of its members. This is the direct data model for the "git diff of the diagram" (Q4).
- **Edge `resolution` field** (`spec/graph.md` section 6): `static`, `dispatch`, `inferred`. Renderers must draw `inferred` edges differently. This separates facts from agent claims (Q2, Q8).
- **Structure is checked, meaning is labelled** (`spec/graph.md` principle 2; `tools/build.ts`): the build fails when a `source.lines` range is outside the file or an id is unknown. The agent loops until the build passes. Copy this check loop (Q8).
- **Source lines are the grounding link** (`spec/graph.md` section 5.1): each member carries `source.file` and `source.lines`; the build copies `code` from the file, so the agent never retypes code (Q2).
- **Flows as named ordered id lists** (`spec/graph.md` section 7): one to three flows tell the story of the change. This maps to sequence-style views without a second diagram type (Q5).
- **Notes with kind `risk | behaviour | note` and `refs`** (`spec/graph.md` section 8): a ready model for reviewer-facing findings (Q9).
- **Change-scoped, one hop of context** (`spec/graph.md` principle 1): the graph is not a whole-codebase map. This is the clutter control for a change view (Q1).
- **ELK at build time, measured widths at view time** (`tools/layout.ts`, `viewer/src/place.ts`): the page needs no layout engine for small graphs. ELK options: layered, direction RIGHT, `considerModelOrder` for small graphs and free order above a size threshold.
- **Prose files as components** (`SKILL.md` step 5): config files get `const` members, Markdown gets `section` members named by heading. Useful because config-harness is full of docs and config.

## What not to copy
- Klar pseudocode per function (`spec/klar.md`): too detailed for the owner's "product functionality, not UML" goal. Keep a one-line summary instead.
- Cards-with-members as the only view: it is a code-level view. Product-level abstraction (Q3) needs a feature layer above it, which OpenArc does not have.
- Single static HTML output: the dashboard needs a live, queryable view with a question box (Q9). Reuse the graph JSON, not the page.
- Node 22.18 type-stripping and TypeScript 7 as build assumptions: the dashboard runs on Bun.

## License
MIT. Code and spec can be reused with the copyright notice.
