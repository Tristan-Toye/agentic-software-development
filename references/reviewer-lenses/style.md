## LENS: style

Can a human read this? Your working question: **should this block become a
well-named private method?**

- **Extraction candidates** — a block with one describable purpose, or one that
  forces the reader to hold intermediate state, or one reachable only through
  three or more levels of nesting. The test: if you can name it better than a
  comment could describe it, it wants to be a method.
- **Mixed altitude** — a function narrating high-level steps that drops into
  byte arithmetic. Each function reads at one level.
- **Nesting and conditionals** — guard clauses over arrow-shaped code; a
  compound boolean that deserves a named predicate.
- **Expression complexity** — a chained one-liner a maintainer must unroll; a
  magic value that recurs and wants a name.
- **Duplicated fragments** inside `SCOPE` that one helper would unify.
- **Documentation** — does each public member carry the language's
  documentation form, and does it match the promise in `CONTRACT`? A docstring
  that drifted from the contract is a CR.
- **Naming** — does a name say what the thing is, in the project's vocabulary?

Respect the surrounding idiom. In a file of 10-line functions a 60-line
addition is a flag; in a procedural codebase, imposing your granularity on one
function is churn. A pure rename with no structural change is a note. Brace
placement and formatter territory are never a CR. Every extraction you request
must be behaviour-preserving, and you must name the new method.

**Every style CR must state, or it is a note and not a CR:**

1. The location, at `path:line`.
2. The observable reading cost — the nesting depth, the body length against
   the file's norm, the mixed altitude, the intermediate state a reader must
   hold. "Hard to read" with no observable is taste.
3. The change, named — the new method name, the guard clause, the predicate.
