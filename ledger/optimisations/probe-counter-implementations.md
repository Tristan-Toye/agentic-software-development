# Counter-implementations for property tests (PROBE), shadow first

Status: **parked**. Decided 2026-11-18: later, in shadow. Depends on #420 (property tests from
checkable promises, usage surface in the mutation check).

## Problem

A property can be true and still too weak. "The output is sorted" passes for a function that always
returns `[]`; "decode(encode(x)) == x" passes when both are the identity. Today a loose property is
found only in Phase 6, and only when one of the per-line mutants of the mutation check happens to
fall into the loophole. When it is found there, an implementer round has already been spent against
the loose test.

## Idea

After the unit-test author returns (end of Phase 4), a counter-implementation agent gets the
contract (stubs plus docstrings) and the property tests, never a real implementation, and tries to
write a body that is wrong against the contract but passes every property test of the surface. A
body it manages to write is a concrete loophole: it goes back to the test author as a weak line,
quoted, before Phase 5.

Source: PROBE, counter-implementations against LLM-written properties; reported about +9.8%
mutation score. Related: PGS (a property must reject known-wrong outputs), Vikram et al. (about one
LLM-written property in five is unsound; coverage of documented properties is low).

## Trigger to start the shadow

When #420 has run on at least 5 builds with property lines, count from `## Build log`:

- mutants on property lines that survived, against those killed;
- survivors whose only failing input was the pinned example (routed as weak lines).

If property-line survivors are rare (under about 1 in 10), close this as not worth it. Otherwise
start the shadow.

## Shadow design (to build when triggered)

- Runs after Phase 4 on each surface with property lines, under the shadow quota (3, sequential),
  output written to `.git/asd-drivers/driver-shadow/` and ignored for every decision.
- One agent per surface, on the same model tier as the unit-test author; blind to the dossier and
  any implementation, like that author.
- Record per surface: counter-implementations attempted, ones that passed every property, and the
  checklist line each loophole sits on.
- Compare afterwards with Phase 6: did the mutation check find the same loophole later, and at what
  cost (rounds, tokens)?

## Decide after the shadow

Enable it for real only if it finds loopholes the mutation check misses, or finds them at least a
round earlier, often enough to pay for one agent per surface.
