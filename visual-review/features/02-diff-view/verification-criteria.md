# Verification criteria

| ID | Criterion | How to check | Fail when |
|---|---|---|---|
| VC-01 | `to-be` output has zero items with status `removed`. | Run on 5 fixtures. | Any removed item appears. |
| VC-02 | `as-is` output has zero items with status `added`. | Same fixtures. | Any added item appears. |
| VC-03 | Counts of added, changed and removed in `diff.json` equal the counts of items with that status in the `changes` output. | Compare. | Any count differs. |
| VC-04 | A parent's status is `changed` if any member is not `context`, else `context`. | Fixture S2. | Parent status differs. |
| VC-05 | A plan that removes a missing element, or adds an existing one, fails with a named issue. | Two fixtures. | The diff passes. |
| VC-06 | Every planned element with no stub signature behind it has `resolution: inferred`. | Fixture S7 and one counter-fixture with a signature. | A proven element is `inferred`, or an unproven one is not. |
| VC-07 | A flow of 13 or more changed steps fails with `over_budget`. | Fixture with 13 steps. | It passes. |
| VC-08 | A repeated step run collapses to one counted step. | Fixture S8. | The run stays expanded. |
| VC-09 | Context nodes keep identical coordinates in all three modes. | Layout once with VR-03, read coordinates per mode. | Any coordinate differs. |
| VC-10 | Output uses two mark channels: a status field and a non-colour mark field in the render data. | Read render data. | A status has colour only. |
