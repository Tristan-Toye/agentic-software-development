# Behaviour scenarios

**S1. Open a run with diagrams.**
Given a run whose diagram folder holds a valid merged graph,
when the owner opens the run page,
then the diagram panel shows the graph in `changes` mode above the question card.

**S2. No external request.**
Given the run page is open,
when the browser network log is read,
then every request goes to the dashboard origin.

**S3. Hostile label.**
Given a node label `<img src=x onerror=alert(1)>`,
when the diagram renders,
then the label shows as literal text and no script runs.

**S4. Mode switch.**
Given the diagram is in `changes` mode,
when the owner picks `to-be`,
then removed items disappear and no node moves.

**S5. Inspect.**
Given a node with evidence `crates/harness/src/x.rs:40`,
when the owner clicks the node,
then the inspector shows the description in product words, the evidence list and the evidence tier.

**S6. Group open and close.**
Given a group of 6 modules drawn as one box,
when the owner opens it,
then the 6 modules show inside, and the layout of other nodes does not move.

**S7. Inferred edge.**
Given an edge with `resolution: inferred`,
when it draws,
then it uses a dashed stroke and a legend entry explains it.

**S8. Walkthrough.**
Given a flow of 8 steps,
when the owner starts walkthrough,
then one step at a time is highlighted, and next and back move one step.

**S9. Large graph.**
Given a graph above the size limit for thorough layout,
when the server lays it out,
then it uses the cheaper ELK setting and the page still opens.

**S10. No diagrams.**
Given a run with no diagram folder,
when the owner opens the page,
then the page shows no diagram panel and no error.
