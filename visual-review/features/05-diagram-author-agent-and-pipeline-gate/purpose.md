# Purpose

The contract-review step sits in Phase 3, before the fan-out, where a wrong plan costs the least ([current-state](../../research/synthesis/current-state.md), B3). The diagram step belongs next to it, but not inside it: the contract reviewer is blind on purpose ([Q7](../../research/synthesis/q7-pipeline-placement.md)).

VR-05 adds the step and a mechanical gate so that no build skips it by accident.
