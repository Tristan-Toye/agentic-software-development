# Testability

- Store: the `Line` type allows extra keys, so add tests that write and read a line with `anchor` and check that the old tests still pass ([Q6](../../research/synthesis/q6-approval-and-comments.md); `shared/questions/store.ts:6-12` per [current-state](../../research/synthesis/current-state.md), B2).
- API: call `POST /api/questions/:id/messages` with `kind: followup` and an `anchor`; check the question stays open.
- UI: the same page test method as VR-03.
- ID stability: generate two graphs from one fixture with a small change; check that unchanged elements keep their IDs. This depends on the ID rule of VR-01.
- Hard to test: owner behaviour. Use the group B measures of [Q9](../../research/synthesis/q9-measuring-value.md).
