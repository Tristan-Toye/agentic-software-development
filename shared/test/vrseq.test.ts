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
  test("the label is text on the arrow beside the sender, between the two lifelines, not a card", () => {
    const L = seqLayout(g3()), b = L.boxes.m1
    expect(b.x).toBeGreaterThanOrEqual(cxOf(L, "lane:A"))
    expect(b.x + b.w).toBeLessThanOrEqual(cxOf(L, "lane:C"))
    expect(b.y + b.h).toBeLessThan(L.arrows!.m1.points[0][1])
    expect(b.h).toBeLessThan(30)
    expect(L.texts!.m1).toEqual(["m1"])
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

// ---- renderer fixes and hierarchical folding (PR C) ----
import { collapseGraph, seqFold, SEQ_BUDGET, wrapWords, visualReview } from "../questions/vrlayout.ts"
const big = (n: number, lanes = 3): any => {
  const ls = Array.from({ length: lanes }, (_, i) => ({ id: "lane:" + i, label: "Lane " + i }))
  const nodes = Array.from({ length: n }, (_, i) => msg("m" + (i + 1), "lane:" + (i % lanes), "lane:" + ((i + 1) % lanes), i + 1))
  return { schema_version: "1.0.0", view: "seq", title: "seq - big", meta: {}, lanes: ls, nodes, edges: [], flows: [{ id: "flow:main", label: "big", status: "context", lanes: [], after: nodes.map((x: any) => x.id), evidence: ev }] }
}
describe("seq lane headers fit, rows are compact", () => {
  test("a long lane label wraps whole inside a column wide enough, and the lifeline starts below the header", () => {
    const g = big(2); g.lanes[0].label = "Repository index builder service (worker pool)"
    const L = seqLayout(g)
    expect(L.texts!["lane:0"].join(" ")).toBe("Repository index builder service (worker pool)")
    expect(L.texts!["lane:0"].length).toBeGreaterThan(1)
    expect(L.headH).toBeGreaterThanOrEqual(14 + 16 * L.texts!["lane:0"].length)
    const w = L.boxes["lane:0"].w, widest = Math.max(...L.texts!["lane:0"].map((t) => t.length)) * 7.6
    expect(w).toBeGreaterThanOrEqual(widest)
    // neighbouring headers never overlap
    const a = L.boxes["lane:0"], b = L.boxes["lane:1"]
    expect(a.x + a.w).toBeLessThanOrEqual(b.x)
    // the first row starts below the header
    expect(L.boxes.m1.y).toBeGreaterThan(L.headH!)
  })
  test("a message row is far smaller than the old 90 px card row", () => {
    const L = seqLayout(big(6)), ys = ["m1", "m2", "m3"].map((id) => L.arrows![id].points[0][1])
    expect(ys[1] - ys[0]).toBeLessThan(40)
  })
  test("the page draws the label as halo text on the arrow, no card, and keeps the minimap off a sequence", () => {
    for (const s of ["paint-order", "sqMsg", "L.texts[n.id]", "L.headH", "if (L.arrows) { ov.hidden = true; return; }"]) expect(PAGE).toContain(s)
  })
})

describe("seq folds hierarchically, never crops", () => {
  test("within the budget nothing folds", () => {
    const r = seqFold(big(SEQ_BUDGET.messages))
    expect(r.fold).toEqual([])
    expect(r.graph.groups).toBeUndefined()
  })
  test("over the message budget: mechanical refs of at most the group budget, every message kept", () => {
    const g = big(20), r = seqFold(g)
    expect(r.fold.length).toBeGreaterThan(1)
    for (const id of r.fold) expect(r.graph.nodes.filter((n: any) => n.group === id).length).toBeLessThanOrEqual(SEQ_BUDGET.group)
    expect(r.graph.nodes).toHaveLength(20)
    expect(r.graph.nodes.every((n: any) => n.group)).toBe(true)
  })
  test("a huge sequence gets two levels at most; the top level is what opens folded", () => {
    const r = seqFold(big(80))
    const by = new Map<string, any>(r.graph.groups.map((x: any) => [x.id, x]))
    const depth = (id: string): number => (by.get(id)?.parent ? 1 + depth(by.get(id).parent) : 1)
    expect(Math.max(...r.graph.groups.map((x: any) => depth(x.id)))).toBeLessThanOrEqual(SEQ_BUDGET.maxLevels)
    expect(r.fold.every((id) => !by.get(id).parent)).toBe(true)
    expect(r.fold.length).toBeLessThanOrEqual(SEQ_BUDGET.group * 2)
  })
  test("author groups are used as they are; the top-level ones open folded", () => {
    const g = big(14)
    g.groups = [{ id: "g:a", label: "Setup", status: "context", evidence: [] }, { id: "g:b", label: "Work", status: "added", evidence: [] }, { id: "g:b1", label: "Inner", status: "added", parent: "g:b", evidence: [] }]
    g.nodes.forEach((n: any, i: number) => { n.group = i < 7 ? "g:a" : i < 10 ? "g:b1" : "g:b" })
    const r = seqFold(g)
    expect(r.graph).toBe(g)
    expect(r.fold).toEqual(["g:a", "g:b"])
  })
  test("folding replaces the messages of a ref by one band, in order, with the count and the lanes it touches", async () => {
    const r = seqFold(big(20)), c = collapseGraph({ ...r.graph }, [r.fold[0]])
    const ref = c.nodes.find((n: any) => n.id === r.fold[0])
    expect(ref).toMatchObject({ collapsed: true, members: r.graph.nodes.filter((n: any) => n.group === r.fold[0]).length })
    expect(ref.seq.over.length).toBeGreaterThan(1)
    expect(c.flows[0].after[0]).toBe(r.fold[0])
    expect(c.flows[0].after).toHaveLength(20 - ref.members + 1)
    const L = await layoutGraph(withFlowEdges(c))
    expect(L.texts![ref.id][0]).toContain("(" + ref.members + " msgs)")
    expect(L.boxes[ref.id].h).toBeLessThan(40)
    // the folded diagram is shorter than the open one
    const open = await layoutGraph(withFlowEdges(r.graph))
    expect(L.height).toBeLessThan(open.height)
    expect(open.refs && Object.keys(open.refs).length).toBe(r.graph.groups.length)
  })
  test("the review opens a big seq folded (collapsed = the top-level refs) and the page can expand all", () => {
    const dir = mkdtempSync(join(tmpdir(), "seqfold-")); mkdirSync(join(dir, "seq-preview"))
    writeFileSync(join(dir, "seq-preview", "big.json"), JSON.stringify(big(20)))
    for (const s of ["Expand all", "Fold all", "data-ref", "cur.graph.view === 'seq'"]) expect(PAGE).toContain(s)
    return visualReview(dir, "W-1").then((r) => { const v = r.views["seqpreview:big"]!; expect(v.collapsed!.length).toBeGreaterThan(1); expect(v.issues).toEqual([]) })
  })
  test("wrapWords never drops text", () => {
    const t = "a".repeat(60) + " tail words here"
    expect(wrapWords(t, 20).join("").replace(/ /g, "")).toBe(t.replace(/ /g, ""))
  })
})

describe("seq tabs are on demand", () => {
  test("the page hides the sequence tabs until the Sequence diagrams button opens them; the Flow stays the default", () => {
    for (const s of ["isSeqTab", "Sequence diagrams", "Hide sequences", "syncSeq"]) expect(PAGE).toContain(s)
    expect(PAGE).toContain("|| isSeqTab(k)")
  })
})

describe("the change diagram opens on the numbered Flow", () => {
  test("L1 is selected on open for a dossier; a committed diagram keeps its first view", () => {
    expect(PAGE).toContain("(!data.mapPath && data.views.L1) ? 'L1' : have[0]")
  })
})
