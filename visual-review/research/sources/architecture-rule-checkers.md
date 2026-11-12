# Architecture rule checkers: dependency-cruiser, ArchUnit, jQAssistant, architecture-check, ArchiLens

- **Type**: tool (group; architecture drift and rule checks)
- **Repo / site**: https://github.com/sverweij/dependency-cruiser , https://github.com/TNG/ArchUnit , https://github.com/jQAssistant/jqassistant , https://github.com/EnWithAI/architecture-check , https://github.com/saurabh-oss/archilens
- **License / cost**: dependency-cruiser MIT; ArchUnit Apache-2.0; jQAssistant GPL-3.0; architecture-check MIT (README); ArchiLens NOASSERTION in GitHub API (check). All free.
- **Self-host**: yes.

## Capabilities
- **dependency-cruiser**: validates JS and TS dependencies against rules, and outputs graphs (dot, mermaid and others) (README). Not for Rust.
- **ArchUnit**: architecture rules as Java unit tests (layers, cycles, naming) (https://github.com/TNG/ArchUnit). Not for Rust.
- **jQAssistant**: scans Java projects into a Neo4j graph and checks rules in Cypher (https://github.com/jQAssistant/jqassistant). Not for Rust.
- **architecture-check**: compares a declared C4 (Structurizr DSL subset) or FINOS CALM model in git with a model derived from code, and reports drift with evidence and a status `verified_static_fact`, `hypothesis` or `unknown`; it never reports "absent" for runtime calls. JS and TS module graph, compose, Kubernetes; version 0.3.261110, early release (https://github.com/EnWithAI/architecture-check README). It can write the derived model as Structurizr DSL or CALM.
- **ArchiLens**: Tree-sitter static analysis with optional LLM; hierarchical drill-down L0 to L3; posts an architecture diff on PRs; fails CI on declared layer and fan-out rules (https://github.com/saurabh-oss/archilens README, comparison table).
- **Rust equivalents**: `cargo deny` for crate bans, and `cargo-modules` cycle detection (`inspiration-repos/cargo-modules/src/command/dependencies/cycles/`). config-harness already uses `deny.toml` (`/Users/tristan.toye/Documents/personal/repos/config-harness/deny.toml`).

## Development resources
- https://github.com/sverweij/dependency-cruiser/blob/main/doc/rules-reference.md : rule syntax.
- https://calm.finos.org : CALM architecture language.

## Alternatives
- LikeC4 and Structurizr model checks: [likec4.md](likec4.md), [structurizr.md](structurizr.md).

## Comparison
| Tool | Languages | Declared model | Checks | Diff on PR |
|---|---|---|---|---|
| dependency-cruiser | JS and TS | rules | yes | no |
| ArchUnit | Java | tests | yes | no |
| jQAssistant | Java | Cypher rules | yes | no |
| architecture-check | JS and TS plus infra | C4 or CALM | drift | no |
| ArchiLens | tree-sitter languages (Rust not confirmed) | rules | yes | yes |

## Recommendation
**SKIP** as dependencies (no Rust support). **USE** two ideas. First, the evidence status `verified_static_fact | hypothesis | unknown` from architecture-check, merged with OpenArc's edge `resolution` (Q8). Second, the "declared versus derived" comparison to detect map drift in the living map (Q7).
