# Generating Software Architecture Description from Source Code using Reverse Engineering and Large Language Model

- **Source**: https://arxiv.org/abs/2511.05165
- **PDF**: `sources/sad-from-source-reverse-eng-llm.pdf` (file path from the repo root: `visual-review/sources/sad-from-source-reverse-eng-llm.pdf`)
- **Relevant to**: Reverse engineering for a component diagram, then LLM abstraction and LLM state machines. Informs the split between extracted facts and LLM abstraction.

## Summary
The paper proposes semi-automated generation of software architecture descriptions from source code (p1). It joins reverse engineering with an LLM (p1). The static view comes from a reverse-engineering tool that extracts a component diagram (p3, p3-p4). The LLM filters architecturally significant core components by prompt engineering (p3, p4). The behavioural view is a state machine diagram per component. A few-shot prompt with a step list produces it (p5). The demonstration uses C++ examples (p1). The LLM can abstract the component diagram and represent complex behaviour, especially with domain knowledge in few-shot examples (p1). The evidence is a demonstration, not a large benchmark.

## Unique insights and approaches
- Extract the full component diagram first, then ask the LLM to select the significant parts (p3, p4). Abstraction becomes a filter on extracted facts. The filter cannot invent components.
- The behavioural view has a fixed step list: understand code, extract states, describe states, extract transitions and triggers, build the diagram (p5). Use a similar fixed procedure for sequence diagrams.
- Domain knowledge in few-shot examples improves behaviour output (p1). Feature descriptions from the product docs can serve as such examples.

## Content table for lookup
| Section / topic | Pages | What you find there | Use during design of |
|---|---|---|---|
| Static view extraction | 3 | Component diagram from reverse engineering | Q2 |
| Abstraction with LLM | 3-4 | Core component filter | Q1 |
| Behavioural view: state machines | 5 | Step list prompt | Q5 |
| Conclusion | 9 | Limits and future work | Q8 |

Page numbers are PDF page numbers of the file in `sources/`.
