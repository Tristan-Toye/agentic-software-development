// The graph hash the review lines carry (graphHash) must equal the one scripts/vr_promote.py computes.
// Run: rtk proxy bun test shared/test/vrhash.test.ts
import { expect, test } from "bun:test"
import { readFileSync } from "node:fs"
import { graphHash } from "../questions/vrlayout.ts"

// Same constant as scripts/vr_promote.py HASH_CASE_EXPECTED.
const EXPECTED = "8ce8ade17ad88b468fe732549001fa366d19a7ee5fba0c509ad28cd8b081434a"
test("graphHash of the shared fixture", () => {
  const g = JSON.parse(readFileSync(new URL("../../references/visual-review/fixtures/hash-case.json", import.meta.url), "utf8"))
  expect(graphHash(g)).toBe(EXPECTED)
})
