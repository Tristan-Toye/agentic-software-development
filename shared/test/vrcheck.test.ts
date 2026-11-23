// Visual review: the shared text-fit rule (vrtext.ts, pinned to scripts/vr_graph.py by one fixture) and the geometry check of a computed layout (vrcheck.ts).
// Run: bun test shared/test/vrcheck.test.ts
import { afterAll, describe, expect, test } from "bun:test"
import { mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { checkLayout, segmentHitsBox } from "../questions/vrcheck.ts"
import { closeLayout, layoutGraph, visualReview } from "../questions/vrlayout.ts"
import { PAGE } from "../questions/page.ts"
import { vrFitLabel, vrNodeWidth } from "../questions/vrtext.ts"

const REF = join(import.meta.dir, "..", "..", "references", "visual-review")
const FIX = join(REF, "fixtures")
const load = (n: string) => JSON.parse(readFileSync(join(FIX, n), "utf8"))
afterAll(() => closeLayout())

describe("text fit: one rule for the layout, the page and vr_graph.py", () => {
  const fx = JSON.parse(readFileSync(join(REF, "text-fit.fixtures.json"), "utf8"))
  for (const c of fx.cases) {
    test(`"${c.label.slice(0, 40)}"${c.stereotype ? " (stereotype)" : ""}${c.folded ? " (folded)" : ""}: width, lines, clipped as pinned`, () => {
      const w = vrNodeWidth(c.label, c.folded)
      expect(w).toBe(c.width)
      const f = vrFitLabel(c.label, w, c.stereotype)
      expect({ lines: f.lines, clipped: f.clipped, needed: f.needed }).toEqual({ lines: c.lines, clipped: c.clipped, needed: c.needed })
    })
  }
  test("the pinned cases hold both polarities", () => {
    expect(fx.cases.some((c: any) => c.clipped)).toBe(true)
    expect(fx.cases.some((c: any) => !c.clipped)).toBe(true)
  })
  test("the page draws labels with the shared function and the one constant set, not its own guess", () => {
    expect(PAGE).toContain("function vrFitLabel")
    expect(PAGE).toContain('"charPx":6.6')
    expect(PAGE).not.toContain("/ 6.4)")
    expect(PAGE).not.toContain("function vrWrap")
  })
})

describe("segmentHitsBox", () => {
  const b = { x: 100, y: 100, w: 50, h: 40 }
  test("through the inside: yes; beside it or along its border: no", () => {
    expect(segmentHitsBox([0, 120], [300, 120], b)).toBe(true)
    expect(segmentHitsBox([125, 0], [125, 300], b)).toBe(true)
    expect(segmentHitsBox([0, 90], [300, 90], b)).toBe(false)
    expect(segmentHitsBox([0, 100], [300, 100], b)).toBe(false)
    expect(segmentHitsBox([0, 120], [90, 120], b)).toBe(false)
  })
})

describe("checkLayout over the fixtures' ELK layouts", () => {
  for (const f of readdirSync(FIX).filter((x) => /^(w145-l[012]|removed|minimal|label-at-limit)\.json$/.test(x))) {
    test(f + " lays out clean", async () => {
      const g = load(f)
      expect(checkLayout(g, await layoutGraph(g))).toEqual([])
    })
  }
})

describe("checkLayout finds what is wrong (adversarial layouts)", () => {
  const graph = {
    view: "L2",
    nodes: [{ id: "a", label: "A" }, { id: "b", label: "B" }, { id: "c", label: "C" }],
    edges: [{ id: "e1", source: "a", target: "c", label: "calls" }],
  }
  const good: any = {
    boxes: { a: { x: 0, y: 0, w: 130, h: 50 }, b: { x: 0, y: 100, w: 130, h: 50 }, c: { x: 300, y: 0, w: 130, h: 50 } },
    edges: { e1: [[130, 25], [300, 25]] },
    labels: { e1: { x: 180, y: 5, w: 60, h: 15 } },
  }
  const codes = (L: any, g: any = graph) => checkLayout(g, L).map((i) => i.code)
  test("a clean layout has no issue", () => expect(codes(good)).toEqual([]))
  test("two overlapping node boxes", () => {
    expect(codes({ ...good, boxes: { ...good.boxes, b: { x: 60, y: 20, w: 130, h: 50 } } })).toContain("node_overlap")
  })
  test("an edge label on a node box", () => {
    expect(codes({ ...good, labels: { e1: { x: 20, y: 105, w: 60, h: 15 } } })).toContain("label_overlap")
  })
  test("an edge segment through a node that is not an endpoint, but not through its own endpoints", () => {
    const L = { ...good, boxes: { ...good.boxes, b: { x: 180, y: 0, w: 60, h: 50 } }, edges: { e1: [[130, 25], [300, 25]] } }
    const r = checkLayout(graph, L)
    expect(r.filter((i) => i.code === "edge_through_node").map((i) => i.where)).toEqual(["e1,b"])
  })
  test("an edge that ends at a group may run past the group's members", () => {
    const g = { view: "L2", groups: [{ id: "G" }], nodes: [{ id: "m", label: "M", group: "G" }, { id: "c", label: "C" }], edges: [{ id: "e", source: "G", target: "c" }] }
    const L: any = { boxes: { G: { x: 0, y: 0, w: 200, h: 100 }, m: { x: 20, y: 30, w: 130, h: 50 }, c: { x: 400, y: 30, w: 130, h: 50 } }, edges: { e: [[10, 55], [400, 55]] } }
    expect(codes(L, g)).toEqual([])
  })
  test("a label that needs more lines than its box draws", () => {
    const g = { view: "L2", nodes: [{ id: "a", label: "x".repeat(70) }], edges: [] }
    const r = checkLayout(g, { boxes: { a: { x: 0, y: 0, w: 130, h: 50 } }, edges: {} } as any)
    expect(r.map((i) => i.code)).toEqual(["label_clipped"])
    expect(r[0].where).toBe("a")
  })
  test("a partial layout never throws", () => {
    expect(checkLayout(graph, { boxes: {}, edges: {} } as any)).toEqual([])
    expect(checkLayout(null, null)).toEqual([])
  })
})

describe("the review shows the check as a warning beside the view", () => {
  test("a label that cannot fit is reported in the view's issues, and the view still has its layout", async () => {
    const dir = mkdtempSync(join(tmpdir(), "vrcheck-"))
    for (const v of ["l0", "l1", "l2"]) {
      const g = load(`w145-${v}.json`)
      if (v === "l2") g.nodes[0].label = "verylongwordthatcannotwrapinsideanynodebox".repeat(2)
      writeFileSync(join(dir, `${v}.json`), JSON.stringify(g))
    }
    const r = await visualReview(dir, "W-145")
    const all = Object.values(r.views).flatMap((v: any) => v?.issues ?? [])
    expect(all.some((i: any) => i.code === "label_clipped")).toBe(true)
  })
})
