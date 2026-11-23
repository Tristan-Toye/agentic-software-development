// VR Sequence previews: the auto-drafted seq graphs in <dossier>/seq-preview/ are tabs next to Decisions, never part of the review.
// Run: bun test shared/test/vrpreview.test.ts
import { afterAll, describe, expect, test } from "bun:test"
import { mkdirSync, mkdtempSync, symlinkSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { collapsedView, closeLayout, previewStemOf, previewStems, reviewViews, tabViews, viewFile, visualReview } from "../questions/vrlayout.ts"
import { reviewState } from "../questions/vrreview.ts"
import { PAGE } from "../questions/page.ts"

afterAll(() => closeLayout())

const ev = [{ tier: "inferred", note: "test" }]
const meta = { repo: "r", commit: "c", generated_by: "agent" }
const msg = (id: string, lane: string, to: string, order: number) => ({ id, label: "Step " + id, kind: "message", status: "added", lane, order, seq: { to, arrow: "->>" }, evidence: ev })
const seq = (extra: any = {}): any => ({
  schema_version: "1.0.0", view: "seq", title: "Read a page of events", meta: { ...meta, auto_draft: "built mechanically from the L1 flow", routing_inferred: 2, ...extra },
  lanes: [{ id: "lane:A", label: "A" }, { id: "lane:B", label: "B" }],
  nodes: [msg("m1", "lane:A", "lane:B", 1), msg("m2", "lane:B", "lane:A", 2), msg("m3", "lane:A", "lane:A", 3)], edges: [],
  flows: [{ id: "flow:x", label: "x", status: "added", lanes: ["lane:A", "lane:B"], after: ["m1", "m2", "m3"], evidence: ev }],
})
const l1 = () => ({
  schema_version: "1.0.0", view: "L1", title: "t", meta, lanes: [{ id: "la", label: "A" }],
  nodes: [1, 2, 3].map((i) => ({ id: "s" + i, label: i === 2 ? "Reads the rows" : "Step " + i, kind: "step", status: "added", lane: "la", evidence: ev })), edges: [],
  flows: [{ id: "f", label: "F", status: "added", after: ["s1", "s2", "s3"], evidence: ev }],
})
const setup = (): string => {
  const dir = mkdtempSync(join(tmpdir(), "vrpv-"))
  writeFileSync(join(dir, "l1.json"), JSON.stringify(l1()))
  mkdirSync(join(dir, "seq-preview"))
  writeFileSync(join(dir, "seq-preview", "seq-auto-f.json"), JSON.stringify(seq()))
  return dir
}

describe("sequence previews", () => {
  test("a preview is a view of its own, found by stem; bad stems, links and other names are not", () => {
    const dir = setup()
    expect(previewStems(dir)).toEqual(["seq-auto-f"])
    expect(previewStemOf("seqpreview:seq-auto-f")).toBe("seq-auto-f")
    expect(previewStemOf("seqpreview:../x")).toBeNull()
    expect(previewStemOf("arch:seq-auto-f")).toBeNull()
    expect(viewFile(dir, "seqpreview:seq-auto-f")).toBe(join(dir, "seq-preview", "seq-auto-f.json"))
    expect(viewFile(dir, "seqpreview:other")).toBeNull()
    symlinkSync(join(dir, "l1.json"), join(dir, "seq-preview", "link.json"))
    writeFileSync(join(dir, "seq-preview", "bad name.json"), "{}")
    writeFileSync(join(dir, "seq-preview", "notes.txt"), "x")
    expect(previewStems(dir)).toEqual(["seq-auto-f"])
  })
  test("it is never a review view: no verdict line, no approval hash", () => {
    const dir = setup()
    expect(tabViews(dir)).not.toContain("seqpreview:seq-auto-f")
    expect(reviewViews(dir).some((v) => v.startsWith("seqpreview:"))).toBe(false)
    const st = reviewState(dir, "W-1")
    expect(Object.keys(st.hashes ?? {}).some((k) => k.includes("seqpreview"))).toBe(false)
    expect(JSON.stringify(st.lines)).not.toContain("seq-auto")
  })
  test("visualReview lays it out as a view marked preview with the auto-draft note; its issues are not the review's", async () => {
    const dir = setup()
    const r = await visualReview(dir, "W-1")
    const v = r.views["seqpreview:seq-auto-f"]!
    expect(v.preview).toBe(true)
    expect(v.note).toContain("mechanically")
    expect(v.title).toBe("Read a page of events")
    expect(v.graph.view).toBe("seq")
    expect(v.layout).not.toBeNull()
    expect(Object.keys(r.views).indexOf("seqpreview:seq-auto-f")).toBeGreaterThan(Object.keys(r.views).indexOf("L1"))
    // negative: a broken preview is a tab with its issue, and does not put an issue on the change
    writeFileSync(join(dir, "seq-preview", "seq-auto-g.json"), JSON.stringify({ ...seq(), view: "L1" }))
    const r2 = await visualReview(dir, "W-1")
    const bad = r2.views["seqpreview:seq-auto-g"]!
    expect(bad.layout).toBeNull()
    expect(bad.issues.length).toBeGreaterThan(0)
    expect(r2.issues.some((i) => i.where.startsWith("seqpreview:"))).toBe(false)
  })
  test("the rev changes with a preview and the real views are unchanged", async () => {
    const dir = setup()
    const a = await visualReview(dir, "W-1")
    writeFileSync(join(dir, "seq-preview", "seq-auto-f.json"), JSON.stringify(seq({ routing_inferred: 3 })))
    const b = await visualReview(dir, "W-1")
    expect(b.rev).not.toBe(a.rev)
    expect(b.views.L1!.layout).toEqual(a.views.L1!.layout)
  })
  test("the L1 view of the same dossier draws its steps in the new shapes and lays out clean", async () => {
    const r = await visualReview(setup(), "W-1")
    expect(r.views.L1!.layout!.shapes).toEqual({ s1: "terminal", s2: "io", s3: "terminal" })
    expect(r.views.L1!.issues).toEqual([])
  })
  test("collapsedView serves a preview and refuses an unknown one", async () => {
    const dir = setup()
    const c = await collapsedView(dir, "seqpreview:seq-auto-f", [])
    expect(c.graph.view).toBe("seq")
    await expect(collapsedView(dir, "seqpreview:nope", [])).rejects.toThrow()
  })
  test("the page draws the Sequence tab with the preview banner and no verdict box", () => {
    expect(PAGE).toContain("seqpreview:")
    expect(PAGE).toContain("(auto draft)")
    expect(PAGE).toContain("not part of the review")
    expect(PAGE).toContain("st.view.indexOf('seqpreview:') !== 0")
  })
})
