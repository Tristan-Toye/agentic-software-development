# Behaviour scenarios

**S1. Added node.**
Given a before graph without node `X` and a planned graph with `X`,
when the diff runs,
then the merged graph has `X` with status `added`.

**S2. Changed node, summarised parent.**
Given a component whose member `m` changed and whose other members did not,
when the diff runs,
then `m` is `changed` and the component is `changed`.

**S3. Removed node stays in `changes`.**
Given a node that the plan removes,
when mode is `changes`,
then the node is in the output with status `removed`.

**S4. Modes.**
Given a merged graph with added, changed, removed and context items,
when mode is `to-be`,
then no `removed` item is in the output; when mode is `as-is`, no `added` item is in the output.

**S5. Stable positions.**
Given a merged graph that was laid out once,
when the owner switches modes,
then each context node has the same coordinates in every mode.

**S6. Stale plan.**
Given a plan that removes node `Y` and `Y` is not in the current base graph,
when the diff runs,
then it fails with `removed_element_missing` and names `Y`.

**S7. Unproven plan element.**
Given a planned edge that no stub signature supports,
when the diff runs,
then the edge has `resolution: inferred`.

**S8. Flow with a repeated run.**
Given a flow in which the same 3-step sequence repeats 5 times,
when the flow diff runs,
then the output has the sequence once with a count of 5.

**S9. Flow over the cap.**
Given a changed flow with 15 steps,
when the flow diff runs,
then it fails with `over_budget` (12 steps).
