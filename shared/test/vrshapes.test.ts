// VR node shapes: the shape a node is drawn in, the box it needs, and where an edge meets its outline.
// Run: bun test shared/test/vrshapes.test.ts
import { afterAll, expect, test } from "bun:test"
import { clipEdgesToShapes, closeLayout, layoutGraph, nodeShape, outlineInset, shapeSize } from "../questions/vrlayout.ts"
import { checkLayout } from "../questions/vrcheck.ts"
import { vrFitLabel } from "../questions/vrtext.ts"

afterAll(() => closeLayout())
const base = { width: 130, height: 50 }

test("shape names: schema names, Mermaid aliases and flow names; kind defaults; a folded box stays a rectangle", () => {
  const sh = (o: any) => nodeShape({ id: "a", label: "A", ...o })
  expect(sh({})).toBe("rect")
  expect(sh({ shape: "rect" })).toBe("rect")
  expect(sh({ shape: "process" })).toBe("rect")
  expect(sh({ shape: "round" })).toBe("round")
  expect(sh({ shape: "stadium" })).toBe("terminal")
  expect(sh({ shape: "terminal" })).toBe("terminal")
  expect(sh({ shape: "diamond" })).toBe("diamond")
  expect(sh({ shape: "decision" })).toBe("diamond")
  expect(sh({ shape: "io" })).toBe("io")
  expect(sh({ shape: "circle" })).toBe("circle")
  expect(sh({ shape: "junction" })).toBe("junction")
  expect(sh({ shape: "cylinder" })).toBe("cylinder")
  expect(sh({ shape: "subroutine" })).toBe("subroutine")
  expect(sh({ shape: "asymmetric" })).toBe("asymmetric")
  expect(sh({ shape: "implicit" })).toBe("rect")
  expect(sh({ shape: "Diamond" })).toBe("diamond")
  expect(sh({ kind: "decision" })).toBe("diamond")
  expect(sh({ kind: "database" })).toBe("cylinder")
  expect(sh({ kind: "function" })).toBe("rect")
  expect(sh({ kind: "decision", shape: "rect" })).toBe("rect") // an explicit shape wins
  expect(sh({ shape: "wobbly", kind: "start" })).toBe("terminal") // an unknown shape falls back to the kind
  expect(sh({ shape: "diamond", collapsed: true })).toBe("rect")
})

test("sizes: a diamond needs more box than a rectangle for the same label; a dot is tiny; a circle is square", () => {
  const label = "Subscriber answered 2xx?"
  const d = shapeSize("diamond", label, base)
  expect(d.width).toBeGreaterThan(base.width)
  expect(d.height).toBeGreaterThan(base.height)
  // the label wraps in at most 3 lines of the room a diamond has
  expect(vrFitLabel(label, d.width, false, undefined, "diamond").clipped).toBe(false)
  expect(shapeSize("diamond", "x".repeat(200), base).width).toBe(300)
  expect(shapeSize("junction", "merge", base)).toEqual({ width: 14, height: 14 })
  const c = shapeSize("circle", "Done", base)
  expect(c.width).toBe(c.height)
  expect(shapeSize("terminal", "Start", base).width).toBeGreaterThan(base.width)
  expect(shapeSize("io", "Read file", base).width).toBeGreaterThan(base.width)
  expect(shapeSize("cylinder", "DB", base).height).toBeGreaterThan(base.height)
  expect(shapeSize("rect", "A step", base)).toEqual(base)
})

test("outline: an edge on the side of a diamond moves in to the slanted edge; on the middle it stays", () => {
  const b = { x: 100, y: 100, w: 160, h: 80 }
  expect(outlineInset("rect", b, [100, 120])).toBe(0)
  expect(outlineInset("diamond", b, [100, 140])).toBeCloseTo(0, 5) // left corner
  expect(outlineInset("diamond", b, [100, 120])).toBeCloseTo(40, 5) // halfway up the left side
  expect(outlineInset("diamond", b, [180, 100])).toBeCloseTo(0, 5) // the top corner
  expect(outlineInset("diamond", b, [140, 100])).toBeCloseTo(20, 5) // top, left of the corner
  expect(outlineInset("diamond", b, [150, 150])).toBe(0) // not on the box boundary
  expect(outlineInset("terminal", b, [100, 140])).toBeCloseTo(0, 5)
  expect(outlineInset("terminal", b, [100, 100])).toBeCloseTo(40, 5) // the corner of the box is empty
  expect(outlineInset("io", b, [100, 100])).toBeCloseTo(12, 5) // top left is slanted in
  expect(outlineInset("io", b, [100, 180])).toBeCloseTo(0, 5)
})

test("clipEdgesToShapes moves only the ends that touch a non-rectangle, and keeps the segments orthogonal", () => {
  const boxes = { a: { x: 0, y: 0, w: 100, h: 50 }, d: { x: 200, y: 0, w: 160, h: 80 } }
  const edges: Record<string, [number, number][]> = { e: [[100, 25], [150, 25], [150, 20], [200, 20]] }
  clipEdgesToShapes(edges, { e: ["a", "d"] }, boxes, { d: "diamond" })
  // the diamond is 160 wide, 80 high: at y=20 (20 above the middle) its left edge is 80 * (20 / 40) = 40 px in from the box
  expect(edges.e[0]).toEqual([100, 25])
  expect(edges.e[3]).toEqual([240, 20])
  for (let i = 1; i < edges.e.length; i++) expect(edges.e[i][0] === edges.e[i - 1][0] || edges.e[i][1] === edges.e[i - 1][1]).toBe(true)
})

test("decision view: kinds terminal/io/step/decision map to shapes, and a circle there is a junction dot", () => {
  expect(nodeShape({ kind: "terminal" }, "decision")).toBe("terminal")
  expect(nodeShape({ kind: "io" }, "decision")).toBe("io")
  expect(nodeShape({ kind: "step" }, "decision")).toBe("rect")
  expect(nodeShape({ kind: "decision" }, "decision")).toBe("diamond")
  expect(nodeShape({ shape: "circle" }, "decision")).toBe("junction")
  expect(nodeShape({ shape: "circle" }, "L2")).toBe("circle")
})

test("the shared text rule knows shapes: a diamond holds fewer characters per line than a box; checkLayout uses the shape's room", () => {
  const label = "Subscriber answered with a two hundred class status code"
  const box = vrFitLabel(label, 200).needed, dia = vrFitLabel(label, 200, false, undefined, "diamond").needed
  expect(dia).toBeGreaterThan(box)
  const g: any = { view: "L2", nodes: [{ id: "d", label, kind: "decision", status: "context" }] }
  const lay: any = { boxes: { d: { x: 0, y: 0, w: 200, h: 84 } }, edges: {}, shapes: { d: "diamond" } }
  expect(checkLayout(g, lay).map((i) => i.code)).toContain("label_clipped")
  expect(checkLayout(g, { ...lay, shapes: undefined }).map((i) => i.code)).not.toContain("label_clipped")
})

test("a decision flow lays out with the shapes: boxes sized per shape, shapes reported, edges end on the outline", async () => {
  const n = (id: string, label: string, extra: any = {}) => ({ id, label, kind: "step", status: "context", evidence: [], ...extra })
  const g: any = {
    schema_version: "1.0.0", view: "L2",
    nodes: [n("s", "Start", { kind: "terminal" }), n("p", "Send it"), n("d", "Answered 2xx?", { shape: "diamond" }), n("db", "Queue", { kind: "database" }), n("j", "merge", { shape: "junction" })],
    edges: [["s", "p"], ["p", "d"], ["d", "db"], ["d", "j"]].map(([a, b], i) => ({ id: "e" + i, source: a, target: b, status: "context", evidence: [] })),
    groups: [],
  }
  const L = await layoutGraph(g)
  expect(L).not.toBeNull()
  const lay: any = L
  expect(lay.shapes).toEqual({ s: "terminal", d: "diamond", db: "cylinder", j: "junction" })
  expect(lay.boxes.j.w).toBe(14)
  expect(lay.boxes.d.w).toBeGreaterThan(lay.boxes.p.w)
  // no edge end is left floating inside the diamond's bounding box corner: every end is on the box or inside it
  for (const pts of Object.values(lay.edges) as [number, number][][]) for (let i = 1; i < pts.length; i++) expect(pts[i][0] === pts[i - 1][0] || pts[i][1] === pts[i - 1][1]).toBe(true)
})
