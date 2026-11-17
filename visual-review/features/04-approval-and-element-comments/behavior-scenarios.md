# Behaviour scenarios

**S1. Verdict OK.**
Given an open question with a diagram,
when the owner marks node `A` as OK,
then the page shows `A` as checked and the count goes up by 1.

**S2. Verdict Question.**
Given node `B`,
when the owner marks it Question and types text,
then a follow-up line is stored with `anchor.element = B`, and the question stays open.

**S3. Verdict Change.**
Given node `C`,
when the owner marks it Change and types text,
then the draft answer includes the text and the element ID.

**S4. Batch send.**
Given 14 elements with 12 checked,
when the owner sends the answer,
then one answer line is stored, and the question closes.

**S5. First answer final.**
Given a closed question,
when a second answer arrives,
then the store refuses it.

**S6. Regeneration.**
Given comments on element `B`,
when the diagram regenerates after a Change,
then `B` keeps its ID and its comments stay attached.

**S7. Removed element.**
Given a comment on an element that the new graph no longer has,
when the page loads,
then the comment shows as "element gone" and is not lost.

**S8. Slack.**
Given the question mirrors to Slack,
when the owner reads it there,
then the text stands alone with the counts and the dashboard link.
