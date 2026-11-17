// VR architecture diagrams as JSON (scripts/vr_mermaid.py): a seq view lays out as lanes top to bottom, its frames get a box, and a
// Mermaid doc with a sibling JSON opens as a graph. Run: bun test shared/test/vrseq.test.ts
import { afterAll, describe, expect, test } from "bun:test"
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { checkGraph, closeLayout, elkInput, frameBoxes, isLaneView, layoutGraph, seqKind, seqLayout, seqReceiver, withFlowEdges } from "../questions/vrlayout.ts"
import { collapsedDiagram, openDiagram, siblingJson } from "../questions/vrmap.ts"
import { PAGE } from "../questions/page.ts"

afterAll(() => closeLayout())

const ev = [{ tier: "inferred", note: "test" }]
const msg = (id: string, lane: string, to: string, order: number) => ({ id, label: id, kind: "message", status: "context", lane, order, seq: { to, arrow: "->>" }, evidence: ev })
const seq = (): any => ({
  schema_version: "1.0.0", view: "seq", title: "seq - demo", meta: {},
  lanes: [{ id: "lane:A", label: "A" }, { id: "lane:B", label: "B" }],
  nodes: ["m1", "m2", "m3", "m4"].map((id, i) => msg(id, i % 2 ? "lane:B" : "lane:A", i % 2 ? "lane:A" : "lane:B", i + 1)),
  edges: [],
  flows: [{ id: "flow:main", label: "demo", status: "context", lanes: ["lane:A", "lane:B"], after: ["m1", "m2", "m3", "m4"], evidence: ev }],
  frames: [
    { id: "frame:alt:x", kind: "alt", label: "ok", first: "m2", last: "m4", branches: [{ kind: "else", label: "failed", first: "m4" }] },
    { id: "frame:opt:y", kind: "opt", first: "m3", last: "m3" },
  ],
})

describe("seq lays out like the L1 flow", () => {
  test("isLaneView holds for L1 and seq only", () => {
    expect(["L1", "seq"].every(isLaneView)).toBe(true)
    expect(["L0", "L2", "c1", "c2", "c3", "c4", "map", "full"].some(isLaneView)).toBe(false)
  })
  test("the ELK input has lanes as compound nodes and direction DOWN", () => {
    const { root, direction } = elkInput(seq(), "thorough")
    expect(direction).toBe("DOWN")
    expect(root.children.map((c: any) => c.id)).toEqual(["lane:A", "lane:B"])
    expect(root.children[0].children.map((c: any) => c.id)).toEqual(["m1", "m3"])
    expect(elkInput({ ...seq(), view: "c2", lanes: undefined }, "thorough").direction).toBe("RIGHT")
  })
  test("the checker accepts seq and c1-c4; withFlowEdges chains the steps", () => {
    for (const v of ["seq", "c1", "c2", "c3", "c4"]) expect(checkGraph({ ...seq(), view: v }).filter((i) => i.code === "view")).toEqual([])
    const g = withFlowEdges(seq())
    expect(g.edges.map((e: any) => e.source + ">" + e.target)).toEqual(["m1>m2", "m2>m3", "m3>m4"])
  })
  test("the steps run top to bottom in order", async () => {
    const L = await layoutGraph(withFlowEdges(seq()))
    expect(L.direction).toBe("DOWN")
    const y = (id: string) => L.boxes[id].y
    expect(y("m1")).toBeLessThan(y("m2"))
    expect(y("m2")).toBeLessThan(y("m3"))
    expect(y("m3")).toBeLessThan(y("m4"))
    expect(L.boxes["lane:A"].x).toBeLessThan(L.boxes["lane:B"].x)
  })
})

describe("frames", () => {
  test("a frame is a box around its first..last steps; a nested one is smaller", async () => {
    const g = withFlowEdges(seq())
    const L = await layoutGraph(g)
    const f = L.frames!["frame:alt:x"], n = L.frames!["frame:opt:y"]
    for (const id of ["m2", "m3", "m4"]) {
      const b = L.boxes[id]
      expect(f.x).toBeLessThanOrEqual(b.x)
      expect(f.y).toBeLessThan(b.y)
      expect(f.x + f.w).toBeGreaterThanOrEqual(b.x + b.w)
      expect(f.y + f.h).toBeGreaterThanOrEqual(b.y + b.h)
    }
    expect(f.y + f.h).toBeLessThanOrEqual(L.height)
    expect(n.w).toBeLessThan(f.w)
    expect(n.y).toBeGreaterThan(f.y)
  })
  test("frameBoxes skips a frame whose steps are unknown and is undefined without frames", () => {
    expect(frameBoxes({ ...seq(), frames: [] }, {})).toBeUndefined()
    expect(frameBoxes({ ...seq(), frames: [{ id: "f", kind: "loop", first: "zz", last: "m1" }] }, { m1: { x: 0, y: 0, w: 1, h: 1 } })).toBeUndefined()
  })
  test("the page draws frames and class members", () => {
    expect(PAGE).toContain("vrfr")
    expect(PAGE).toContain("it.members.join")
    expect(PAGE).toContain("function vrLane")
  })
})

describe("openDiagram prefers the sibling JSON", () => {
  const repo = mkdtempSync(join(tmpdir(), "vrseq-"))
  const arch = join(repo, "docs", "architecture")
  mkdirSync(arch, { recursive: true })
  const md = "# t\n\n```mermaid\nsequenceDiagram\n  A->>B: hi\n```\n"
  writeFileSync(join(arch, "seq-demo.md"), md)
  writeFileSync(join(arch, "seq-demo.json"), JSON.stringify(seq()))
  writeFileSync(join(arch, "seq-plain.md"), md)
  writeFileSync(join(arch, "seq-multi.md"), md)
  writeFileSync(join(arch, "seq-multi.2.json"), JSON.stringify({ ...seq(), title: "second" }))
  writeFileSync(join(arch, "seq-multi.1.json"), JSON.stringify({ ...seq(), title: "first" }))
  writeFileSync(join(arch, "seq-multi-other.json"), JSON.stringify(seq()))
  const listed = ["demo", "plain", "multi"].map((s) => ({ path: "docs/architecture/seq-" + s + ".md", kind: "seq", title: s }))

  test("siblingJson names <stem>.json, else the first <stem>.<n>.json, else nothing", () => {
    expect(siblingJson(repo, listed[0].path)).toBe("docs/architecture/seq-demo.json")
    expect(siblingJson(repo, listed[2].path)).toBe("docs/architecture/seq-multi.1.json")
    expect(siblingJson(repo, listed[1].path)).toBeUndefined()
    expect(siblingJson(repo, "docs/architecture/seq-demo.json")).toBeUndefined()
  })
  test("a seq doc opens as a graph in the L1 slot with its layout and frames", async () => {
    const r: any = await openDiagram(repo, listed[0].path, listed)
    expect(r.kind).toBe("graph")
    expect(Object.keys(r.views)).toEqual(["L1"])
    expect(r.views.L1.layout.direction).toBe("DOWN")
    expect(Object.keys(r.views.L1.layout.frames)).toHaveLength(2)
    expect(r.views.L1.tab).toBe("Sequence")
  })
  test("a doc with several JSONs opens the first block; a doc without JSON stays raw Mermaid", async () => {
    expect(((await openDiagram(repo, listed[2].path, listed)) as any).views.L1.graph.title).toBe("first")
    const raw: any = await openDiagram(repo, listed[1].path, listed)
    expect(raw.kind).toBe("mermaid")
    expect(raw.text).toContain("sequenceDiagram")
  })
  test("a c2 doc goes in the L2 slot, and its folded layout is served by the .md path", async () => {
    writeFileSync(join(arch, "c2-x.md"), "# c2\n\n```mermaid\nflowchart LR\n  a --> b\n```\n")
    writeFileSync(join(arch, "c2-x.json"), JSON.stringify({ schema_version: "1.0.0", view: "c2", title: "c2", meta: {}, groups: [{ id: "group:g", label: "g", status: "context" }],
      nodes: [{ id: "node:a", label: "a", kind: "container", status: "context", group: "group:g", evidence: ev }, { id: "node:b", label: "b", kind: "container", status: "context", group: "group:g", evidence: ev }],
      edges: [{ id: "e", source: "node:a", target: "node:b", kind: "depends", status: "context", evidence: ev }] }))
    const l = [{ path: "docs/architecture/c2-x.md", kind: "c2", title: "c2" }]
    const r: any = await openDiagram(repo, l[0].path, l)
    expect(Object.keys(r.views)).toEqual(["L2"])
    const c = await collapsedDiagram(repo, l[0].path, l, ["group:g"])
    expect(c.collapsed).toEqual(["group:g"])
  })
})

describe("seq draws sender-to-receiver arrows", () => {
  const m = (id: string, lane: string, to: string, order: number, arrow = "->>", extra: any = {}) => ({ ...msg(id, lane, to, order), seq: { to, arrow }, ...extra })
  const g3 = (): any => ({
    schema_version: "1.0.0", view: "seq", title: "t", meta: {},
    lanes: [{ id: "lane:A", label: "A" }, { id: "lane:B", label: "B" }, { id: "lane:C", label: "C" }],
    nodes: [m("m1", "lane:A", "lane:C", 1), m("m2", "lane:C", "lane:C", 2, "->>"), m("m3", "lane:C", "lane:B", 3, "-->>"), m("m4", "lane:B", "lane:A", 4, "-)")],
    edges: [], flows: [{ id: "flow:main", label: "x", status: "context", lanes: [], after: ["m1", "m2", "m3", "m4"], evidence: ev }],
  })
  const cxOf = (L: any, lane: string) => L.boxes[lane].x + L.boxes[lane].w / 2
  test("each arrow runs from the sender's lifeline to the receiver's, in either direction", () => {
    const L = seqLayout(g3()), a = L.arrows!
    expect(a.m1.points.map((p) => p[0])).toEqual([cxOf(L, "lane:A"), cxOf(L, "lane:C")])
    expect(a.m3.points.map((p) => p[0])).toEqual([cxOf(L, "lane:C"), cxOf(L, "lane:B")])
    expect(a.m4.points.map((p) => p[0])).toEqual([cxOf(L, "lane:B"), cxOf(L, "lane:A")])
    expect([a.m1.from, a.m1.to]).toEqual(["lane:A", "lane:C"])
    expect(a.m1.points[0][1]).toBe(a.m1.points[1][1])
  })
  test("messages run top to bottom in order, each arrow below the one before", () => {
    const a = seqLayout(g3()).arrows!
    const ys = ["m1", "m2", "m3", "m4"].map((id) => Math.max(...a[id].points.map((p) => p[1])))
    expect([...ys].sort((p, q) => p - q)).toEqual(ys)
    expect(new Set(ys).size).toBe(4)
  })
  test("the label sits between the two lifelines, on the arrow", () => {
    const L = seqLayout(g3()), b = L.boxes.m1
    expect(b.x + b.w / 2).toBeCloseTo((cxOf(L, "lane:A") + cxOf(L, "lane:C")) / 2, 0)
    expect(b.y).toBeLessThan(L.arrows!.m1.points[0][1])
  })
  test("a self-call is a loop that leaves and returns to the same lifeline", () => {
    const L = seqLayout(g3()), s = L.arrows!.m2, cx = cxOf(L, "lane:C")
    expect(s.self).toBe(true)
    expect(s.points.length).toBe(4)
    expect(s.points[0][0]).toBe(cx)
    expect(s.points[3][0]).toBe(cx)
    expect(Math.max(...s.points.map((p) => p[0]))).toBeGreaterThan(cx)
    expect(s.points[3][1]).toBeGreaterThan(s.points[0][1])
    expect(L.boxes.m2.x).toBeGreaterThan(cx)
  })
  test("the arrow kind comes from seq.arrow, or from call_kind on an L1 step; call_kind wins", () => {
    const k = (arrow: string) => seqKind({ seq: { arrow } })
    expect(["->>", "->", "-x"].map(k)).toEqual(["call", "call", "call"])
    expect(["-->>", "-->", "--x"].map(k)).toEqual(["reply", "reply", "reply"])
    expect(["-)", "--)"].map(k)).toEqual(["async", "async"])
    expect(["call", "reply", "async", "publish"].map((c) => seqKind({ call_kind: c }))).toEqual(["call", "reply", "async", "async"])
    expect(seqKind({ call_kind: "reply", seq: { arrow: "->>" } })).toBe("reply")
    expect(seqKind({})).toBe("call")
    const a = seqLayout(g3()).arrows!
    expect([a.m1.kind, a.m3.kind, a.m4.kind]).toEqual(["call", "reply", "async"])
  })
  test("an L1-style step with `to` and no seq block still gets its arrow; a note has none", () => {
    expect(seqReceiver({ to: "lane:B" })).toBe("lane:B")
    expect(seqReceiver({ kind: "note", seq: { to: "lane:B" } })).toBeUndefined()
    const g = g3()
    g.nodes[0] = { id: "m1", label: "step", kind: "step", status: "added", lane: "lane:A", order: 1, to: "lane:B", call_kind: "async", evidence: ev }
    g.nodes.push({ id: "n1", label: "note", kind: "note", status: "context", lane: "lane:A", order: 5, seq: { position: "right of" }, evidence: ev })
    g.flows[0].after.push("n1")
    const L = seqLayout(g)
    expect(L.arrows!.m1).toMatchObject({ from: "lane:A", to: "lane:B", kind: "async" })
    expect(L.arrows!.n1).toBeUndefined()
    expect(L.boxes.n1.x).toBeGreaterThan(cxOf(L, "lane:A"))
  })
  test("lifelines are spaced so a long label fits between its two lifelines", () => {
    const g = g3()
    g.nodes[0].label = "x".repeat(60)
    const L = seqLayout(g), b = L.boxes.m1
    expect(b.x).toBeGreaterThanOrEqual(cxOf(L, "lane:A") - 1)
    expect(b.x + b.w).toBeLessThanOrEqual(cxOf(L, "lane:C") + 1)
  })
  test("frames still cover the arrow's whole width", () => {
    const g = g3()
    g.frames = [{ id: "frame:loop:x", kind: "loop", first: "m1", last: "m3" }]
    const L = seqLayout(g), f = L.frames!["frame:loop:x"]
    expect(f.x).toBeLessThanOrEqual(cxOf(L, "lane:A"))
    expect(f.x + f.w).toBeGreaterThanOrEqual(cxOf(L, "lane:C"))
  })
  test("the page draws the arrows by kind and status, and the lifelines, in theme variables", () => {
    for (const s of ["vrSeqArrow", "vrSeqMarkers", "vrlife", "vr-sq-", "L.arrows"]) expect(PAGE).toContain(s)
    expect(PAGE).toContain("'stroke-dasharray': a.kind === 'reply'")
  })
})
