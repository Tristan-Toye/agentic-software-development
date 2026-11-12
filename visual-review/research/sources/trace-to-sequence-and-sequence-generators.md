# Sequence diagrams from runtime or code: Jaeger/OpenTelemetry, mermaid-trace, jigsaw, EventHelix, Diver, tlsd

- **Type**: tool (group; sequence diagram sources)
- **Repo / site**: https://github.com/jaegertracing/jaeger , https://opentelemetry.io , mermaid-trace, jigsaw, https://www.eventhelix.com , Diver, tlsd
- **License / cost**: Jaeger and OpenTelemetry Apache-2.0 (well known); others not re-verified. EventHelix is commercial.
- **Self-host**: Jaeger yes; EventHelix no (paid).

## Capabilities
- **Jaeger / OpenTelemetry**: record real call flows as spans across services; a trace can be rendered as a sequence. Needs instrumented, running code and gives only exercised paths.
- **mermaid-trace** (README read): Python decorators that write a Mermaid sequence diagram while code runs. Python only.
- **jigsaw** (README read): generates sequence diagrams from code for Java; not Rust.
- **EventHelix**: commercial tool for sequence diagrams from specs. SKIP (paid).
- **Diver, tlsd**: small generators found by search; not examined.
- **Static route for Rust**: rust-analyzer call hierarchy gives caller and callee edges; order comes from source order inside a function body ([rust-extraction.md](rust-extraction.md)). Diff of two sequences: `inspiration-repos/analysis/mermaidiff.md`.

## Alternatives
- Mermaid sequence rendering: [mermaid.md](mermaid.md).

## Comparison
| Source | Static or runtime | Rust | Covers unrun paths | Cost |
|---|---|---|---|---|
| OpenTelemetry/Jaeger | runtime | yes (SDK) | no | setup |
| mermaid-trace | runtime | no | no | free |
| jigsaw | static | no | yes | free |
| rust-analyzer calls | static | yes | yes (approx.) | build |

## Recommendation
**BUILD** static sequence extraction from rust-analyzer call hierarchy, marked `inferred` where dispatch is dynamic (Q5). **USE** OpenTelemetry traces later as an optional "verified at runtime" overlay. **SKIP** the rest.
