# Verification criteria

| ID | Criterion | How to check | Fail when |
|---|---|---|---|
| VC-01 | A stored follow-up line has an `anchor` key with `diagram` and `element`. | Read the JSONL after a Question verdict. | The key is missing. |
| VC-02 | The existing store and server tests pass without edits. | Run the suite. | Any old test fails or needs a change. |
| VC-03 | A second answer to a closed question is refused. | Post twice. | The second is stored. |
| VC-04 | A Question verdict leaves the question open. | Read the question state. | The question closes. |
| VC-05 | Element IDs for unchanged elements are equal across two regenerations. | Diff the ID sets. | Any unchanged ID differs. |
| VC-06 | The summary count equals the number of elements with a verdict. | Compare UI and store. | Counts differ. |
| VC-07 | The question has 2 to 4 options and no option text contains the word "approve". | Read the question. | Count or wording fails. (Applies only if OQ-01 option B is chosen.) |
| VC-08 | A comment on a removed element still shows after regeneration. | Fixture S7. | The comment disappears. |
| VC-09 | No question is shipped before OQ-01 has an owner answer. | Check the decision register. | OQ-01 is open and the code is on. |

Note on VC-07: Waived for the visual-review question by owner decision 2026-11-13: the option label 'Approved' is allowed.
