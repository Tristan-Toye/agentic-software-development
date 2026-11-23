## LENS: performance

Can this be faster, or smaller, without changing what it does? Be rigorous:
speculation here costs real churn.

- **Complexity** — the time and space complexity of each operation in `SCOPE`.
  Flag a nested scan over a collection that a set or a dictionary makes linear.
  Flag a repeated linear lookup inside a loop.
- **Work amplification** — a query per element where one batched query works
  (the N+1). A recomputation inside a loop that is loop-invariant. A repeated
  parse, compile, or serialise of the same input.
- **Allocation and copying** — an allocation inside a hot loop. A copy of a
  large buffer that a slice, a span, a view, or a reference would avoid. A
  boxing conversion on a hot path. A string built by repeated concatenation.
- **Memory movement** — a transfer that crosses a boundary more times than it
  needs to: a full read where a stream works, a materialised list where an
  iterator flows through, a round trip that could be one call.
- **Blocking and concurrency** — a synchronous call on an async path, a lock
  held across I/O, a serial loop over independent work.
- **Laziness** — work computed and then discarded.

**Every performance CR must state, or it is a note and not a CR:**

1. The operation, at `path:line`.
2. What it costs now, in complexity terms or in allocations per call, and **the
   input scale that makes the cost matter** — with the evidence for that scale,
   from `CRITERIA`, from `CONTEXT_DOCS`, or from what the code around it does.
3. The proposed change, and what it costs instead.
4. Why the change is behaviour-preserving under `RUN_EVIDENCE`.

A CR that reads `this could be optimised` with no scale and no measurement is
not material. A micro-optimisation on a path that runs one time per request is
not material. Readability lost for an unmeasured gain is a regression, and the
`style` lens will file against you next round.
