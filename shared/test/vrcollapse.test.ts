// VR-03: collapsing groups (collapseGraph, collapsedView, /api/visual-review/layout's core) and the walkthrough order.
// Run: bun test shared/test/vrcollapse.test.ts
import { afterAll, describe, expect, test } from "bun:test"
import { cpSync, mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { readFileSync } from "node:fs"
import { closeLayout, collapseGraph, collapsedView, layoutGraph } from "../questions/vrlayout.ts"
import { vrWalkOrder } from "../questions/vrwalk.ts"
import { PAGE } from "../questions/page.ts"

const FIX = join(import.meta.dir, "..", "..", "references", "visual-review", "fixtures")
const load = (n: string) => JSON.parse(readFileSync(join(FIX, n), "utf8"))
afterAll(() => closeLayout())

describe("collapseGraph", () => {
  test("no ids, or only unknown ids, returns the graph itself", () => {
    const g = load("w145-l2.json")
    expect(collapseGraph(g, [])).toBe(g)
    expect(collapseGraph(g, ["nope", "c-step"])).toBe(g)
  })
  test("a collapsed group becomes one node with its member count; its members go", () => {
    const g = load("w145-l2.json")
    const c = collapseGraph(g, ["g-protocol"])
    expect(c.nodes.find((n: any) => n.id === "c-set")).toBeUndefined()
    expect(c.nodes.find((n: any) => n.id === "c-mig")).toBeUndefined()
    expect(c.groups.map((x: any) => x.id)).not.toContain("g-protocol")
    const box = c.nodes.find((n: any) => n.id === "g-protocol")
    expect(box).toMatchObject({ collapsed: true, members: 2, label: expect.stringMatching(/\(2\)$/), status: "changed" })
    expect(c.nodes).toHaveLength(g.nodes.length - 2 + 1)
  })
  test("edges to members re-route to the box; edges inside it disappear", () => {
    const g = load("w145-l2.json")
    const c = collapseGraph(g, ["g-protocol"])
    const by = (id: string) => c.edges.find((e: any) => e.id === id)
    expect(by("d2")).toMatchObject({ source: "c-step", target: "g-protocol" })
    expect(by("d6")).toMatchObject({ source: "c-fix", target: "g-protocol" })
    expect(by("d3")).toBeUndefined() // c-set -> c-mig: both inside
    for (const e of c.edges) {
      const ids = new Set([...c.nodes, ...c.groups].map((x: any) => x.id))
      expect(ids.has(e.source) && ids.has(e.target), e.id).toBe(true)
    }
  })
  test("edges that now join the same two boxes are aggregated with a count", () => {
    const g = load("w145-l2.json")
    const c = collapseGraph(g, ["g-harness", "g-protocol"])
    // d2 (c-step -> c-set) and d10 (g-harness -> g-protocol) both become g-harness -> g-protocol
    const agg = c.edges.filter((e: any) => e.source === "g-harness" && e.target === "g-protocol")
    expect(agg).toHaveLength(1)
    expect(agg[0]).toMatchObject({ id: "agg:g-harness>g-protocol", label: "2 links", count: 2, resolution: "static" })
    expect(agg[0].status).toBe("changed") // added + context mixed
    expect(c.edges.filter((e: any) => e.id === "d1" || e.id === "d4")).toHaveLength(0) // inside g-harness
  })
  test("a nested collapsed group is hidden by the outer one", () => {
    const g = { schema_version: "1.0.0", view: "L2", nodes: [
      { id: "a", label: "a", kind: "module", status: "added", group: "inner" },
      { id: "b", label: "b", kind: "module", status: "context", group: "outer" },
      { id: "z", label: "z", kind: "module", status: "context" }],
    groups: [{ id: "outer", label: "Outer", status: "context" }, { id: "inner", label: "Inner", status: "context", parent: "outer" }],
    edges: [{ id: "e1", source: "z", target: "a", kind: "calls", status: "context" }] }
    const c = collapseGraph(g, ["inner", "outer"])
    expect(c.groups).toHaveLength(0)
    expect(c.nodes.map((n: any) => n.id).sort()).toEqual(["outer", "z"])
    expect(c.nodes.find((n: any) => n.id === "outer")).toMatchObject({ members: 2, status: "changed" })
    expect(c.edges).toMatchObject([{ id: "e1", source: "z", target: "outer" }])
  })
  test("the input graph is not changed", () => {
    const g = load("w145-l2.json"), before = JSON.stringify(g)
    collapseGraph(g, ["g-harness"])
    expect(JSON.stringify(g)).toBe(before)
  })
})

describe("layout of a collapsed graph", () => {
  test("every shown node has a box and every edge a path; it is deterministic", async () => {
    const c = collapseGraph(load("w145-l2.json"), ["g-harness"])
    const L = await layoutGraph(c)
    for (const n of c.nodes) expect(L.boxes[n.id], n.id).toBeDefined()
    for (const e of c.edges) expect(L.edges[e.id], e.id).toBeDefined()
    expect(await layoutGraph(structuredClone(c))).toEqual(L)
  })
  test("a collapsed group is smaller than the open one, and opening it again gives the original layout", async () => {
    const g = load("w145-l2.json")
    const open = await layoutGraph(g), shut = await layoutGraph(collapseGraph(g, ["g-harness"]))
    expect(shut.boxes["g-harness"].w * shut.boxes["g-harness"].h).toBeLessThan(open.boxes["g-harness"].w * open.boxes["g-harness"].h)
    expect(await layoutGraph(collapseGraph(g, []))).toEqual(open)
  })
  test("the groups and nodes outside the collapsed group stay in the same reading order", async () => {
    const g = load("w145-l2.json")
    const open = await layoutGraph(g), shut = await layoutGraph(collapseGraph(g, ["g-protocol"]))
    const rest = Object.keys(shut.boxes).filter((id) => open.boxes[id] && id !== "g-protocol")
    const key = (L: any) => rest.slice().sort((a, b) => L.boxes[a].x - L.boxes[b].x || L.boxes[a].y - L.boxes[b].y)
    // the layer (column) of each outside element is unchanged
    const col = (L: any, id: string) => Object.values<any>(L.boxes).filter((b) => b.x < L.boxes[id].x - 20).length > 0
    expect(rest.length).toBeGreaterThan(5)
    expect(key(shut).map((id) => col(shut, id))).toEqual(key(open).map((id) => col(open, id)))
  })
  test("collapsedView reads the file, ignores unknown ids and L1 lanes, and refuses a bad view", async () => {
    const d = mkdtempSync(join(tmpdir(), "vrc-"))
    for (const v of ["l0", "l1", "l2"]) cpSync(join(FIX, `w145-${v}.json`), join(d, `${v}.json`))
    const r = await collapsedView(d, "L2", ["g-protocol", "nope"])
    expect(r.collapsed).toEqual(["g-protocol"])
    expect(r.layout.boxes["g-protocol"]).toBeDefined()
    expect(r.graph.nodes.find((n: any) => n.id === "g-protocol").collapsed).toBe(true)
    expect((await collapsedView(d, "L1", ["g-protocol"])).collapsed).toEqual([])
    await expect(collapsedView(d, "L9", [])).rejects.toThrow(/view must be/)
  })
})

describe("vrWalkOrder", () => {
  const box = (x: number, y: number, w = 100, h = 40) => ({ x, y, w, h })
  const lay = (boxes: any, edges: any = {}, direction = "RIGHT") => ({ view: "L2", mode: "thorough", direction, width: 1000, height: 500, boxes, edges })
  test("only added, changed and removed elements are listed", () => {
    const g = { nodes: [{ id: "a", status: "context" }, { id: "b", status: "added" }, { id: "c", status: "removed" }, { id: "d", status: "changed" }], edges: [{ id: "e", source: "a", target: "b", status: "context" }] }
    const L = lay({ a: box(0, 0), b: box(200, 0), c: box(400, 0), d: box(600, 0) })
    expect(vrWalkOrder(g, L)).toEqual(["b", "c", "d"])
  })
  test("layers first (left to right), then top to bottom inside a layer", () => {
    const g = { nodes: ["late", "lowB", "topB", "first", "right"].map((id) => ({ id, status: "added" })), edges: [] }
    const L = lay({ right: box(400, 0), first: box(0, 20), lowB: box(0, 100), topB: box(2, 50), late: box(0, 300) })
    expect(vrWalkOrder(g, L)).toEqual(["first", "topB", "lowB", "late", "right"])
  })
  test("with step numbers the order field leads, nodes without one come last, position breaks ties", () => {
    const g = { nodes: [{ id: "x", status: "added" }, { id: "s2", status: "added", order: 2 }, { id: "s1", status: "added", order: 1 }, { id: "s3", status: "added", order: 2 }], edges: [] }
    expect(vrWalkOrder(g, lay({ x: box(0, 0), s2: box(500, 0), s1: box(900, 300), s3: box(0, 400) }))).toEqual(["s1", "s2", "s3", "x"])
  })
  test("the layout position decides when the order field is absent or equal", () => {
    const g = { nodes: ["a", "b", "c", "d"].map((id) => ({ id, status: "changed" })), edges: [] }
    expect(vrWalkOrder(g, lay({ a: box(0, 300), b: box(0, 0), c: box(300, 100), d: box(300, 0) }))).toEqual(["b", "a", "d", "c"])
  })
  test("direction DOWN: rows are the layers, left to right inside a row", () => {
    const g = { nodes: ["a", "b", "c", "d"].map((id) => ({ id, status: "added" })), edges: [] }
    expect(vrWalkOrder(g, lay({ a: box(300, 0), b: box(0, 0), c: box(150, 200), d: box(0, 200) }, {}, "DOWN"))).toEqual(["b", "a", "d", "c"])
  })
  test("an edge follows its source node; groups are included; boxless elements are skipped", () => {
    const g = { groups: [{ id: "grp", status: "changed" }], lanes: [{ id: "lane", status: "context" }], nodes: [{ id: "n1", status: "added", group: "grp" }, { id: "n2", status: "added" }, { id: "ghost", status: "added" }],
      edges: [{ id: "e1", source: "n1", target: "n2", status: "added" }, { id: "e2", source: "ghost", target: "n2", status: "added" }] }
    const L = lay({ grp: box(0, 0, 150, 120), n1: box(14, 40), n2: box(400, 40) }, { e1: [[114, 60], [400, 60]], e2: [[500, 500], [510, 500]] })
    expect(vrWalkOrder(g, L)).toEqual(["grp", "n1", "e1", "n2", "e2"])
  })
  test("a partial graph or layout does not throw", () => {
    expect(vrWalkOrder({}, {})).toEqual([])
    expect(vrWalkOrder(null, null)).toEqual([])
  })
  test("the W-145 flow view walks in step order (the order field)", async () => {
    const g = load("w145-l1.json"), L = await layoutGraph(g)
    const ids = vrWalkOrder(g, L)
    const stepsOnly = ids.filter((id) => id.startsWith("s"))
    expect(stepsOnly).toEqual(["s2", "s4", "s5", "s6", "s7", "s8", "s9", "s10"])
    expect(ids).not.toContain("s1")
    expect(ids).toContain("f1")
    expect(ids.indexOf("f1")).toBeLessThan(ids.indexOf("s2")) // an edge sorts right after its source s1, which has no change itself
  })
  test("the page ships the same function: it parses as plain JS and gives the same order", () => {
    const m = PAGE.match(/function vrWalkOrder\([\s\S]*?\n}\n/)
    expect(m).not.toBeNull()
    const fn = new Function(m![0] + "; return vrWalkOrder")()
    const g = load("w145-l2.json")
    const L = lay(Object.fromEntries([...g.groups, ...g.nodes].map((x: any, i: number) => [x.id, box((i % 4) * 200, Math.floor(i / 4) * 90)])))
    expect(fn(g, L)).toEqual(vrWalkOrder(g, L))
  })
})

describe("page code of the canvas", () => {
  test("no diagram text goes through innerHTML (VC-02) and no outside origin is named (VC-01)", () => {
    const vr = PAGE.slice(PAGE.indexOf("function vrBuild"), PAGE.indexOf("// ---- questions ----"))
    expect(vr.length).toBeGreaterThan(5000)
    expect(vr).not.toMatch(/innerHTML|outerHTML|insertAdjacentHTML|document\.write/)
    expect(vr).not.toMatch(/https?:\/\/(?!www\.w3\.org)/)
    expect(vr).toContain("/api/visual-review/layout?")
  })
  test("a clipped node label ends with an ellipsis; the inspector and the node title carry the full label (FU-02)", () => {
    const vr = PAGE.slice(PAGE.indexOf("function vrBuild"), PAGE.indexOf("// ---- questions ----"))
    expect(vr).toMatch(/wl\.length > 3\) lines\[2\] = lines\[2\]\.replace\([^)]*'(…|\\u2026)'\)/) // the page is served ASCII-escaped
    expect(vr).toMatch(/vrDom\('h3', '', it\.label \|\|/) // inspector: the whole label, unwrapped
    expect(vr).toMatch(/const tip = [^\n]*it\.label/) // accessible title on the node
    // The wrapped text is not the only place the label goes: a clip never replaces the data.
    expect(vr).not.toMatch(/n\.label\s*=\s*/)
  })
  test("every colour of the canvas CSS comes from the theme variables", () => {
    const css = PAGE.split("\n").filter((l) => /^\.vr|^\.vr[a-z]/.test(l)).join("\n")
    const noVars = css.replace(/var\(--[\w-]+\)/g, "")
    expect(noVars).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(/)
  })
})

describe("the whole-repo product map opens folded", () => {
  const ev = [{ tier: "source", path: "a.md", line: 1 }]
  const mk = (id: string, group: string) => ({ id, label: id, kind: "feature", status: "context", group, evidence: ev })
  const map = {
    schema_version: "1.0.0", view: "map", title: "4 features in 2 product areas", meta: { repo: "r", commit: "c", generated_by: "extractor" },
    groups: [{ id: "area:a", label: "A", status: "context" }, { id: "area:b", label: "B", status: "context" }],
    nodes: [mk("feature:A-01", "area:a"), mk("feature:A-02", "area:a"), mk("feature:B-01", "area:b"), mk("feature:B-02", "area:b")],
    edges: ["A-01:B-01", "A-02:B-01", "A-02:B-02", "B-02:B-01"].map((p) => {
      const [s, t] = p.split(":")
      return { id: "edge:builds-on:feature:" + s + ":feature:" + t, source: "feature:" + s, target: "feature:" + t, kind: "depends", label: "builds on", status: "context", evidence: ev }
    }),
  }
  test("openDiagram returns it in the L0 slot with every area to fold; the folded layout aggregates edges with counts", async () => {
    const { mkdtempSync, mkdirSync, writeFileSync } = await import("node:fs")
    const { openDiagram, collapsedDiagram } = await import("../questions/vrmap.ts")
    const root = mkdtempSync(join(tmpdir(), "vrmap-"))
    mkdirSync(join(root, "docs/architecture"), { recursive: true })
    writeFileSync(join(root, "docs/architecture/product-map.json"), JSON.stringify(map))
    const listed = [{ path: "docs/architecture/product-map.json", kind: "product-map", title: map.title }]
    const d: any = await openDiagram(root, listed[0].path, listed)
    expect(Object.keys(d.views)).toEqual(["L0"])
    expect(d.views.L0.collapsed).toEqual(["area:a", "area:b"])
    const f = await collapsedDiagram(root, listed[0].path, listed, d.views.L0.collapsed)
    expect(f.graph.nodes.map((n: any) => n.id).sort()).toEqual(["area:a", "area:b"])
    const agg = f.graph.edges.find((e: any) => e.source === "area:a" && e.target === "area:b")
    expect(agg.label).toBe("builds on ×3")
    expect(f.graph.edges.length).toBe(1) // the edge inside B is dropped
    expect(Object.keys(f.layout.boxes).sort()).toEqual(["area:a", "area:b"])
    closeLayout()
  })
})
