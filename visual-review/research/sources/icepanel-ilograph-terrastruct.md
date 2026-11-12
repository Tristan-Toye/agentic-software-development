# IcePanel, Ilograph, Terrastruct (commercial modelling tools)

- **Type**: service (commercial architecture-modelling SaaS)
- **Repo / site**: https://icepanel.io , https://www.ilograph.com , https://terrastruct.com
- **License / cost**: all three are commercial products. IcePanel lists a free plan (1 editor) and paid plans at $40 and $80 per editor per month, billed annually (https://icepanel.io/pricing, fetched 2026-11-12). Ilograph and Terrastruct pricing pages did not return readable text in this session; cost is not confirmed.
- **Self-host**: no for IcePanel (SaaS, per its site). Ilograph and Terrastruct: not confirmed. D2, which Terrastruct publishes, is open source: see [d2.md](d2.md).

## Capabilities
- IcePanel: C4-style model with levels, "flows" for sequences, diagrams linked to one model, tags, and an API (https://icepanel.io , not evaluated in depth).
- Ilograph: one resource model, perspectives, and an interactive view that shows different slices of the same model (https://www.ilograph.com , not evaluated in depth).
- Terrastruct: hosted D2 editor and the TALA layout engine (https://d2lang.com/tour/tala).

## Development resources
- https://icepanel.io/pricing : plans.
- https://www.ilograph.com/docs : docs (not read in this session).

## Alternatives
- LikeC4 and Structurizr: [likec4.md](likec4.md), [structurizr.md](structurizr.md). Free and self-hostable.

## Comparison
| Option | Model-based | Interactive levels | Diff | Self-host | Cost |
|---|---|---|---|---|---|
| IcePanel | yes | yes | not confirmed | no | free tier, then paid |
| Ilograph | yes | yes (perspectives) | not confirmed | not confirmed | commercial |
| Terrastruct | D2 text | limited | no | D2 only | commercial for TALA and the editor |
| LikeC4 | yes | yes | yes | yes | free |

## Recommendation
**SKIP** as dependencies: the core product must not need a paid subscription. Study their interaction ideas only: IcePanel's levels and flows, and Ilograph's perspectives (one model, many slices) as a model for Q1.
