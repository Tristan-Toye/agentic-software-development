# Behaviour scenarios

**S1. Workspace drift.**
Given the config-harness checkout of 2026-11-12,
when the members script runs,
then it reports `fault` and `step-timing` as missing from c2.

**S2. Cosmetic edit.**
Given a backing file where only a comment changed,
when the stale test runs,
then the element shows "cosmetic change" and is not stale.

**S3. Signature change.**
Given a backing file where a public signature changed,
when the stale test runs,
then the element is stale.

**S4. Plan staleness.**
Given a planned diff that removes element `Z`, and `Z` is gone from the base,
when the plan check runs,
then it reports the plan as stale.

**S5. Banner.**
Given 3 stale elements,
when the owner opens the map,
then the banner says 3 and lists them.

**S6. Browse all.**
Given a repo with a committed map,
when the owner opens the repo view,
then the feature map, flows and containers are listed, each openable on the canvas.

**S7. Seeded table.**
Given `FEATURE-BOOK.md` with H-01 to H-13,
when the table is seeded,
then each feature has at least one link with `path:line` evidence and the signal that made it.

**S8. Drift report.**
Given declared and derived layers that differ,
when the drift check runs,
then it reports each difference as `diverge` or `absent` and changes no file.

**S9. No map yet.**
Given a repo with no committed map,
when the owner opens the repo view,
then the page says so and offers no stale banner.
