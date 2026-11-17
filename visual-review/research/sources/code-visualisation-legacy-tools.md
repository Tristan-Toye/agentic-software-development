# Code maps and software-visualisation tools: Sourcetrail, CodeSee, CodeCharta, emerge, Moose

- **Type**: tool (group)
- **Repo / site**: https://github.com/CoatiSoftware/Sourcetrail , https://github.com/MaibornWolff/codecharta , https://github.com/glato/emerge , https://github.com/moosetechnology/Moose
- **License / cost**: Sourcetrail GPL-3.0, archived; CodeCharta BSD-3-Clause; emerge MIT; Moose MIT (all GitHub API). CodeSee: commercial SaaS, no open repo found (see below). All others free.
- **Self-host**: yes for Sourcetrail, CodeCharta, emerge, Moose. CodeSee: no.

## Capabilities
- **Sourcetrail**: interactive source explorer with a graph view and a code view side by side, for C, C++, Java, Python. Archived; the upstream repo has no push since 2021-12-13 (GitHub API). A community fork `OpenSourceSourceTrail/Sourcetrail` is also archived (GitHub API, last push 2026-09-05). Idea to keep: graph and code views linked, with a bookmarking of paths.
- **CodeSee**: a code-map and PR-review service that showed maps of changed code on pull requests. The owner reports it shut down. I could not confirm this from a primary source in this session; a search for a CodeSee GitHub organisation with open code found nothing, and a guessed GitKraken blog URL returned 404. Treat the shutdown as reported, not verified. Lesson: a living map that depends on a hosted service dies with the service, so keep the map in the repo.
- **CodeCharta**: turns a codebase into a 3D "city of metrics", a domain-language map and a dependency graph (https://github.com/MaibornWolff/codecharta README). Input is a JSON metrics file (`.cc.json`) that other tools write. Pushed 2026-11-12 (GitHub API).
- **emerge**: scans source and shows dependency, filesystem and metric graphs as an interactive web app using force-directed layout; Java, Kotlin, Go, Python, TypeScript and more, not Rust (https://github.com/glato/emerge README language list).
- **Moose**: a platform for software and data analysis in Pharo Smalltalk; metamodels (FAMIX), visual tools such as Roassal (https://github.com/moosetechnology/Moose). Research-grade; Pharo runtime needed.

## Development resources
- https://codecharta.com : docs and demo.
- https://github.com/glato/emerge/wiki : emerge usage.
- https://modularmoose.org : Moose docs.

## Alternatives
- Modern LLM-assisted maps: [llm-repo-explainers.md](llm-repo-explainers.md), [understand-anything.md](understand-anything.md), [codeboarding.md](codeboarding.md).

## Comparison
| Tool | Maintained | Rust support | Output | Idea worth keeping |
|---|---|---|---|---|
| Sourcetrail | no | no | desktop app | linked graph and code views |
| CodeSee | no (reported) | n/a | SaaS | PR map; hosted risk |
| CodeCharta | yes | via generic metrics import (not checked) | 3D city | metrics as a layer on a map |
| emerge | yes (push 2026-08-07) | no | web app | self-contained HTML export |
| Moose | yes | no | Pharo | metamodel discipline |

## Recommendation
**SKIP** all as dependencies: none supports Rust well and none has a diff or feature level. **USE** two ideas: Sourcetrail's linked graph and code panes for the inspector (Q6), and CodeCharta's practice of putting a metric (size, churn) on nodes as a colour or size cue (Q1).
