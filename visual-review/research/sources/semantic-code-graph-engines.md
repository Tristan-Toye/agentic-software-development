# Semantic code graph engines: tree-sitter graph DSL, stack-graphs, CodeQL, SCIP

- **Type**: framework (code-analysis engines)
- **Repo / site**: https://github.com/tree-sitter/tree-sitter-graph , https://github.com/github/stack-graphs , https://github.com/github/codeql , https://github.com/sourcegraph/scip
- **License / cost**: tree-sitter-graph Apache-2.0; stack-graphs Apache-2.0, archived (GitHub API: `archived: true`, last push 2025-09-09); CodeQL queries MIT (GitHub API). The CodeQL CLI binary has its own terms that limit commercial use on private code (https://github.com/github/codeql-cli-binaries/blob/main/LICENSE.md; read before use). SCIP licence not checked.
- **Self-host**: yes for all; CodeQL CLI is free only under its terms.

## Capabilities
- **tree-sitter-graph**: a DSL that builds a graph from tree-sitter parse trees by rules (https://github.com/tree-sitter/tree-sitter-graph). It is the base of stack-graphs.
- **stack-graphs**: name-binding graphs per file, resolved by path search, for precise go-to-definition without a build. GitHub used it for code navigation. The repo is archived, so no new language rules (GitHub API). Rust rules were never complete; not verified here.
- **CodeQL**: compiles code to a relational database and runs Datalog-like queries; call graphs and data flow. Supports Rust (https://codeql.github.com/docs/codeql-language-guides/codeql-for-rust/, not read in this session). Heavy: needs a build to create the database.
- **SCIP and LSIF**: index formats for code navigation (https://github.com/sourcegraph/scip). `rust-analyzer scip` can write a SCIP index (https://rust-analyzer.github.io/book/, not re-read). It gives definitions and references, which is enough for edges between modules and functions.

## Development resources
- https://github.com/sourcegraph/scip : format and tools.
- https://codeql.github.com/docs/ : CodeQL docs.

## Alternatives
- Direct rust-analyzer use: [rust-extraction.md](rust-extraction.md).
- Code-graph MCP servers: [code-graph-mcp-servers.md](code-graph-mcp-servers.md).

## Comparison
| Option | Precision | Build needed | Maintained | Output | Fit for Rust workspace |
|---|---|---|---|---|---|
| tree-sitter-graph | syntax rules | no | yes (last push 2024-12) | graph | high effort, no gain over `syn` |
| stack-graphs | name binding | no | no (archived) | graph | no |
| CodeQL | high | yes | yes | DB | heavy; licence limits |
| SCIP via rust-analyzer | high | no (loads cargo) | yes | protobuf | good option for references |

## Recommendation
**SKIP** tree-sitter-graph, stack-graphs and CodeQL for the core. **USE** SCIP output from rust-analyzer as an option to evaluate for the reference layer, because it avoids writing a `ra_ap_*` helper. Reason: archived or heavy tools add risk with no clear gain over rust-analyzer.
