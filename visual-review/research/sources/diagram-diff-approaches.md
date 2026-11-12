# Diagram diff approaches (pattern survey)

- **Type**: pattern comparison (no single tool)
- **Repo / site**: see linked docs
- **License / cost**: n/a
- **Self-host**: n/a

## Capabilities
- **Structured graph diff** (OpenArc): compute status per node and edge from git diff; render one merged graph with colours. See [change-diagram-skills.md](change-diagram-skills.md).
- **Model-view diff** (LikeC4): compare two revisions of a model and show as-is, changes, to-be views. See [likec4.md](likec4.md).
- **Sequence diff** (mermaidiff): align two message lists, mark added and removed messages.
- **Mermaid colour classes**: add `classDef added/removed` to a text diagram; cheap but loses layout stability.
- **CALM / declared-versus-derived drift** (architecture-check): compare a declared model to one derived from code ([architecture-rule-checkers.md](architecture-rule-checkers.md)).
- **Layout stability**: a diff needs the same node in the same place in both states. ELK with fixed positions for unchanged nodes is the open question; no tool surveyed solves it.

## Comparison
| Approach | Stable layout | Sequence | Machine-checkable | Effort |
|---|---|---|---|---|
| Structured graph diff | merged graph, one layout | no | yes | medium |
| Model-view diff | per view | no | yes | medium |
| Sequence diff | n/a | yes | yes | low |
| Colour classes | no | partial | no | low |
| Declared vs derived | n/a | no | yes | medium |

## Recommendation
**BUILD** the structured graph diff: one merged graph with `added|changed|removed|context`, one layout, toggle between as-is, changes and to-be (Q4). Use the sequence diff algorithm for Q5.
