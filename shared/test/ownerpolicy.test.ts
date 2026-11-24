// The mechanical-question policy: a flaky CI check and a metadata-only approval are never put to the owner.
// Run: bun test shared/test/ownerpolicy.test.ts
import { expect, test } from "bun:test"
import { readFileSync } from "node:fs"

const doc = (p: string): string => readFileSync(new URL("../../" + p, import.meta.url), "utf8").replace(/\s+/g, " ")

test("the session re-runs a red check outside the change itself, within a budget, and never asks", () => {
  for (const p of ["references/work-on/phase-9.md", "commands/resolve-merge.md"]) {
    const t = doc(p)
    expect(t).toContain("gh run rerun <id> --failed")
    expect(t).toMatch(/at most 2 re-runs per head/)
    expect(t).not.toMatch(/is put to the owner with the evidence/)
  }
  const p9 = doc("references/work-on/phase-9.md")
  expect(p9).toContain("suspected flake")
  expect(p9).toContain("not this change, 2 re-runs used")
  expect(p9).toContain("never ask the owner")
})

test("owner-questions.md lists both cases as must-not-ask examples", () => {
  const t = doc("references/owner-questions.md")
  expect(t).toContain("A red CI check outside the change is never a question")
  expect(t).toContain("W-197.Q8")
  expect(t).toContain("An approval that only a metadata field invalidated is never a question")
  expect(t).toContain("W-118.Q7")
  expect(t).toContain("approval carried: metadata only")
})
