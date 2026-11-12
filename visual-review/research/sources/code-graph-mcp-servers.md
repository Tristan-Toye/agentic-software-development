# Code-graph MCP servers: codebase-memory-mcp, CodeGraphContext, crabmap, CogniCode

- **Type**: tool (group; code knowledge graphs exposed over MCP)
- **Repo / site**: https://github.com/DeusData/codebase-memory-mcp , https://github.com/CodeGraphContext/CodeGraphContext , crabmap, CogniCode
- **License / cost**: licences not re-verified for all four; check before any use. Free to run locally.
- **Self-host**: yes, local.

## Capabilities
- **codebase-memory-mcp**: indexes a repo into a local graph and answers structure queries over MCP (analysed and removed from `inspiration-repos` as the repo was not needed for the final ten).
- **CodeGraphContext**: indexes code into a graph database and serves it to AI clients (https://github.com/CodeGraphContext/CodeGraphContext README).
- **crabmap, CogniCode**: Rust-oriented code-graph tools found by search; not examined beyond their READMEs.
- All serve an agent with queries, not a human with a picture. None has diff or sequence views.

## Development resources
- https://github.com/DeusData/codebase-memory-mcp : the repo and README of codebase-memory-mcp.
- https://github.com/CodeGraphContext/CodeGraphContext : the repo and README of CodeGraphContext.
- [rust-extraction.md](rust-extraction.md) : the extraction options this corpus compares them against.

## Alternatives
- Build the extraction ourselves: [rust-extraction.md](rust-extraction.md). The plugin already has `nav` MCP tools (code_find, code_outline, code_item).

## Comparison
| Tool | Store | Rust | Output | Fit |
|---|---|---|---|---|
| codebase-memory-mcp | local graph | yes (claimed) | MCP answers | agent lookup |
| CodeGraphContext | graph DB | claimed | MCP answers | agent lookup |
| crabmap | not checked | yes | not checked | evaluate |
| CogniCode | not checked | yes | not checked | evaluate |

## Recommendation
**SKIP** as dependencies. **USE** one idea: expose our map to agents over the existing `nav` MCP so a planning agent can query facts (Q2). Quick spike worth doing: run crabmap on config-harness and compare to the cargo-modules output.
