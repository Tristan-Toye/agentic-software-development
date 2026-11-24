// The graph hash the review lines carry (graphHash) must equal the one scripts/vr_promote.py computes.
// Run: rtk proxy bun test shared/test/vrhash.test.ts
import { expect, test } from "bun:test"
import { readFileSync } from "node:fs"
import { approvalHash, graphHash } from "../questions/vrlayout.ts"

// Same constant as scripts/vr_promote.py HASH_CASE_EXPECTED.
const EXPECTED = "8ce8ade17ad88b468fe732549001fa366d19a7ee5fba0c509ad28cd8b081434a"
test("graphHash of the shared fixture", () => {
  const g = JSON.parse(readFileSync(new URL("../../references/visual-review/fixtures/hash-case.json", import.meta.url), "utf8"))
  expect(graphHash(g)).toBe(EXPECTED)
})

// Same constant as scripts/vr_promote.py APPROVAL_CASE_EXPECTED: the approval key ignores the metadata.
const APPROVAL_EXPECTED = "9d6ec0a5df120af15542fa3f7e5cf31ecb8edf87c7a2ac9f5a01a8e6a6344285"
const load = () => JSON.parse(readFileSync(new URL("../../references/visual-review/fixtures/approval-case.json", import.meta.url), "utf8"))
test("approvalHash of the shared fixture equals the Python key", () => {
  expect(approvalHash(load())).toBe(APPROVAL_EXPECTED)
})
test("approvalHash ignores the drawn-on commit, the anchors and the line numbers", () => {
  const a = load(), b = load()
  b.meta.commit = "0000000"; b.meta.base_sha = "1111111"; b.meta.branch = "other"
  b.nodes[0].evidence[0].line = 99; b.nodes[0].evidence[0].end_line = 120; b.nodes[0].evidence[0].path = "src/moved.rs"
  expect(approvalHash(b)).toBe(approvalHash(a))
  expect(graphHash(b)).not.toBe(graphHash(a))
})
test("approvalHash changes with a label, an element, an inferred note or the evidence tier", () => {
  const base = approvalHash(load())
  const edits: ((g: any) => void)[] = [
    (g) => { g.nodes[0].label = "Step 1" },
    (g) => { g.nodes.push({ id: "n2", label: "x", evidence: [] }) },
    (g) => { g.nodes[0].evidence[1].note = "other" },
    (g) => { g.nodes[0].evidence[0] = { tier: "inferred", note: "the call" } },
    (g) => { g.meta.repo = "other" },
  ]
  for (const e of edits) { const g = load(); e(g); expect(approvalHash(g)).not.toBe(base) }
})

test("approvalHash ignores an anchor-only change: a renamed file in a code link or a path:LINE in the text", () => {
  const a = load(), b = load()
  b.nodes[0].evidence[0].path = "db/migrations/V2__new_name.sql"; b.nodes[0].evidence[0].note = "renamed"
  b.nodes[0].detail = "Applies db/V2__new_name.sql:40 then returns"
  expect(approvalHash(b)).toBe(approvalHash(a))
  const c = load(); c.nodes[0].detail = "Applies migrations/V12__old_name.sql:14 then stops"
  expect(approvalHash(c)).not.toBe(approvalHash(a))
})
