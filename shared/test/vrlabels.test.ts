// VR canvas, edge labels and wrapping: ELK keeps room for every edge label (the page draws it there), and a long left-to-right chain
// wraps onto more rows near the canvas shape. Run: bun test shared/test/vrlabels.test.ts
import { afterAll, describe, expect, test } from "bun:test"
import { chainSections, closeLayout, edgeLabelSize, hasDiagonal, elkInput, layoutGraph, WRAP_ASPECT } from "../questions/vrlayout.ts"
import { PAGE } from "../questions/page.ts"

afterAll(() => closeLayout())

const ev = [{ tier: "static", ref: "x" }]
/** A chain of `n` nodes, each edge labelled. */
const chain = (n: number, view = "L2") => ({
  version: 1, view, title: "chain",
  nodes: Array.from({ length: n }, (_, i) => ({ id: "n" + i, label: "Node number " + i, kind: "module", status: "context", evidence: ev })),
  edges: Array.from({ length: n - 1 }, (_, i) => ({ id: "e" + i, source: "n" + i, target: "n" + (i + 1), kind: "depends", label: "builds on ×" + (i + 2), status: "context", evidence: ev })),
})
const overlap = (a: any, b: any) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y

describe("edge labels in the layout", () => {
  test("labels:elk-input-carries-the-drawn-size", () => {
    const { root } = elkInput(chain(3), "thorough")
    const l = root.edges[0].labels[0]
    expect(l.text).toBe("builds on ×2")
    expect({ width: l.width, height: l.height }).toEqual(edgeLabelSize("builds on ×2"))
  })
  test("labels:unlabelled-edge-has-none", () => {
    const g: any = chain(2); delete g.edges[0].label
    expect(elkInput(g, "thorough").root.edges[0].labels).toBeUndefined()
  })
  test("labels:every-label-placed-clear-of-boxes-and-each-other", async () => {
    const g = chain(6)
    const L = await layoutGraph(g)
    const labs = Object.values(L.labels ?? {})
    expect(labs.length).toBe(5)
    const boxes = g.nodes.map((n) => L.boxes[n.id])
    for (const b of labs) expect(boxes.some((x) => overlap(b, x))).toBe(false)
    for (let i = 0; i < labs.length; i++) for (let j = i + 1; j < labs.length; j++) expect(overlap(labs[i], labs[j])).toBe(false)
  })
  test("labels:page-draws-at-the-kept-place", () => {
    expect(PAGE).toContain("L.labels && L.labels[e.id]")
  })
})

describe("wrapping", () => {
  test("wrap:right-views-wrap-down-views-do-not", () => {
    expect(elkInput(chain(3), "thorough", true).root.layoutOptions["elk.layered.wrapping.strategy"]).toBe("MULTI_EDGE")
    expect(elkInput(chain(3), "thorough", true).root.layoutOptions["elk.aspectRatio"]).toBe(String(WRAP_ASPECT))
    const l1: any = { ...chain(3, "L1"), lanes: [] }
    expect(elkInput(l1, "thorough", true).root.layoutOptions["elk.layered.wrapping.strategy"]).toBeUndefined()
    expect(elkInput(chain(3), "thorough").root.layoutOptions["elk.layered.wrapping.strategy"]).toBeUndefined()
  })
  test("wrap:small-graph-stays-in-one-row", async () => {
    const L = await layoutGraph(chain(4))
    const ys = new Set(Object.values(L.boxes).map((b) => b.y))
    expect(ys.size).toBe(1)
  })
  test("wrap:a-wrapped-edge-is-drawn-whole", async () => {
    const g = chain(14)
    const L = await layoutGraph(g)
    for (const e of g.edges) {
      const p = L.edges[e.id], s = L.boxes[e.source], t = L.boxes[e.target]
      const near = (q: number[], b: any) => q[0] >= b.x - 2 && q[0] <= b.x + b.w + 2 && q[1] >= b.y - 2 && q[1] <= b.y + b.h + 2
      expect(near(p[0], s)).toBe(true)
      expect(near(p[p.length - 1], t)).toBe(true)
    }
  })
  test("wrap:long-chain-is-not-one-strip", async () => {
    const L = await layoutGraph(chain(14))
    // Unwrapped, 14 nodes in a row are over 2500 px wide and one box tall.
    expect(L.width / L.height).toBeLessThan(4)
  })
})

describe("wrapped edge sections", () => {
  test("sections:chained-by-outgoing-not-array-order", () => {
    const a = { id: "a", outgoingSections: ["b"] }, b = { id: "b", incomingSections: ["a"], outgoingSections: ["c"] }, c = { id: "c", incomingSections: ["b"] }
    expect(chainSections([c, a, b]).map((x) => x.id)).toEqual(["a", "b", "c"])
  })
  test("sections:broken-chain-keeps-given-order", () => {
    const a = { id: "a", outgoingSections: ["zz"] }, b = { id: "b", incomingSections: ["a"] }
    expect(chainSections([b, a]).map((x) => x.id)).toEqual(["b", "a"])
  })
  test("diagonal:detected-only-off-axis", () => {
    const base = { view: "L2", mode: "thorough" as const, direction: "RIGHT" as const, width: 1, height: 1, boxes: {} }
    expect(hasDiagonal({ ...base, edges: { e: [[0, 0], [10, 0], [10, 5]] } })).toBe(false)
    expect(hasDiagonal({ ...base, edges: { e: [[0, 0], [10, 0], [20, 5]] } })).toBe(true)
  })
})

test("size:folded-box-has-room-for-arrow-and-button", () => {
  const g: any = chain(2)
  g.nodes[0].label = "Other modules of the platform (23)"
  const plain = elkInput(g, "thorough").root.children[0].width
  g.nodes[0].collapsed = true
  expect(elkInput(g, "thorough").root.children[0].width).toBe(plain + 36)
})
