import { expect, test } from "bun:test"
import { mkdirSync, mkdtempSync, writeFileSync, existsSync, readFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { readParts, readPreviewComments, writePreviewComment } from "../questions/vrparts.ts"

const dir = () => mkdtempSync(join(tmpdir(), "vrparts-"))
const put = (d: string, rel: string, o: unknown) => { mkdirSync(join(d, rel, ".."), { recursive: true }); writeFileSync(join(d, rel), JSON.stringify(o)) }

test("every part is listed, a none part carries its reason", () => {
  const d = dir()
  for (const v of ["l0", "l1", "l2"]) put(d, v + ".json", { nodes: [] })
  put(d, "parts.json", { decision_none: "No decisions in this change: only data moves", sequence_none: "No sequence: no flow changes", affected_none: "No architecture diagram affected: none anchored", new_none: "No new diagram: the dossier names none" })
  const p = readParts(d)
  expect(p.map((x) => x.key)).toEqual(["L0", "L1", "L2", "decision", "sequence", "affected", "new"])
  expect(p.filter((x) => x.kind === "none").map((x) => x.reason)).toEqual([
    "No decisions in this change: only data moves", "No sequence: no flow changes",
    "No architecture diagram affected: none anchored", "No new diagram: the dossier names none"])
})

test("content, an unchanged statement and an update draft are separate cards", () => {
  const d = dir()
  put(d, "l0.json", {}); put(d, "decision.json", { view: "decision" })
  put(d, "arch/c4-a.json", { view: "c4", meta: { updates_existing: true } })
  put(d, "arch/c4-new.json", { view: "c4" })
  put(d, "seq-preview/seq-x.json", { view: "seq" })
  put(d, "parts.json", { unchanged: [{ stem: "c4-b", reason: "only a body moved" }] })
  const p = readParts(d)
  const by = Object.fromEntries(p.map((x) => [x.key, x]))
  expect(by.decision.kind).toBe("content")
  expect(by.sequence.views).toEqual(["seqpreview:seq-x"])
  expect(by["affected:c4-a"].views).toEqual(["arch:c4-a"])
  expect(by["affected:c4-b"].reason).toBe("Diagram c4-b unchanged: only a body moved")
  expect(by["new:c4-new"].kind).toBe("content")
  expect(by.L1.kind).toBe("none")
})

test("an old review without parts.json still lists every part", () => {
  const d = dir(); put(d, "l1.json", {})
  const p = readParts(d)
  expect(p.length).toBe(7)
  expect(p.find((x) => x.key === "decision")!.reason).toContain("before the parts were mandatory")
})

test("preview comments are kept beside a preview only, never in review.json", () => {
  const d = dir()
  expect(() => writePreviewComment(d, "decision", "useful")).toThrow("not a preview directory")
  writeFileSync(join(d, "PREVIEW-ONLY"), "x")
  writePreviewComment(d, "decision", " useful ")
  expect(readPreviewComments(d).decision.comment).toBe("useful")
  expect(existsSync(join(d, "review.json"))).toBe(false)
  writePreviewComment(d, "decision", "")
  expect(readPreviewComments(d)).toEqual({})
  expect(() => writePreviewComment(d, "../x", "a")).toThrow("plain key")
  expect(JSON.parse(readFileSync(join(d, "comments.json"), "utf8"))).toEqual({})
})

test("the page script still parses and carries the parts panel and the preview view", async () => {
  const { PAGE } = await import("../questions/page.ts")
  const src = String(PAGE)
  const m = /<script>([\s\S]*)<\/script>/.exec(src)
  expect(m).not.toBeNull()
  expect(() => new Function(m![1])).not.toThrow()
  expect(src).toContain("All diagrams (preview)")
  expect(src).toContain("/api/visual-review/preview-comment")
})
