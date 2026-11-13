// VR canvas, every committed view: `full` views open in the L2 slot, big views open folded, aggregated edges say what they merge,
// and the dense folded product map merges both directions of a pair. Run: bun test shared/test/vrfull.test.ts
import { afterAll, describe, expect, test } from "bun:test"
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { aggLabel, closeLayout, collapseGraph, EDGE_LABEL_MAX } from "../questions/vrlayout.ts"
import { collapsedDiagram, openDiagram, openFolds, OPEN_BUDGET } from "../questions/vrmap.ts"
import { PAGE } from "../questions/page.ts"

afterAll(() => closeLayout())

const ev = [{ tier: "source", path: "a.rs", line: 1 }]
const node = (id: string, group?: string, kind = "module") => ({ id, label: id.split(":").pop(), kind, status: "context", ...(group ? { group } : {}), evidence: ev })
const edge = (s: string, t: string, kind = "depends", label?: string) => ({ id: "edge:" + kind + ":" + s + ":" + t, source: s, target: t, kind, status: "context", ...(label ? { label } : {}), evidence: ev })
const base = (view: string, title: string, nodes: any[], edges: any[], groups?: any[]) => ({ schema_version: "1.0.0", view, title, meta: {}, nodes, edges, ...(groups ? { groups } : {}) })

/** A modules-like view: `parts` groups of `per` modules each, a chain edge inside each group and one edge between neighbours. */
function big(parts: number, per: number) {
  const groups: any[] = [], nodes: any[] = [], edges: any[] = []
  for (let p = 0; p < parts; p++) {
    groups.push({ id: "group:c::g" + p, label: "g" + p, status: "context" })
    for (let i = 0; i < per; i++) {
      nodes.push(node("module:c::g" + p + "::m" + i, "group:c::g" + p))
      if (i) edges.push(edge("module:c::g" + p + "::m" + (i - 1), "module:c::g" + p + "::m" + i))
    }
    if (p) edges.push(edge("module:c::g" + (p - 1) + "::m0", "module:c::g" + p + "::m0"))
  }
  return base("full", "Modules of c", nodes, edges, groups)
}

describe("every view kind the list offers opens on the canvas", () => {
  const repo = mkdtempSync(join(tmpdir(), "vrfull-"))
  mkdirSync(join(repo, "docs/architecture"), { recursive: true })
  const put = (name: string, g: any) => writeFileSync(join(repo, "docs/architecture", name), JSON.stringify(g))
  const small = (view: string, title: string) => base(view, title, [node("crate:a", undefined, "crate"), node("crate:b", undefined, "crate")], [edge("crate:a", "crate:b")])
  const cases: [string, string, string, any, string, string][] = [
    ["product-map.json", "product-map", "L0", base("map", "Product map", [node("feature:A-01", "area:a")], [], [{ id: "area:a", label: "A", status: "context" }]), "L0", "Product map"],
    ["crate-map.json", "crate-map", "L2", small("full", "Crate map of r"), "L2", "Crate map"],
    ["crate-calls.json", "crate-calls", "L2", small("full", "Calls between crates of r"), "L2", "Crate calls"],
    ["modules-c.json", "modules", "L2", big(3, 8), "L2", "Modules"],
    ["calls-c.json", "calls", "L2", big(2, 12), "L2", "Calls"],
    ["flow.json", "flow", "L1", small("L1", "Flow"), "L1", "Flow"],
    ["comp.json", "components", "L2", small("L2", "Components"), "L2", "Components"],
  ]
  const listed = cases.map(([f, kind, , g]) => ({ path: "docs/architecture/" + f, kind, title: g.title, view: g.view }))
  for (const c of cases) put(c[0], c[3])
  for (const [f, kind, , g, slot, tab] of cases) {
    test(kind + " (" + g.view + ") opens in slot " + slot + " with a layout and the tab " + tab, async () => {
      const d: any = await openDiagram(repo, "docs/architecture/" + f, listed)
      expect(d.kind).toBe("graph")
      expect(Object.keys(d.views)).toEqual([slot])
      const v = d.views[slot]
      expect(v.issues).toEqual([])
      expect(v.layout).not.toBeNull()
      expect(v.tab).toBe(tab)
      expect(v.title).toBe(g.title)
    })
  }
  test("a mermaid diagram opens as text and the page has no 'does not draw' path", async () => {
    writeFileSync(join(repo, "docs/architecture/c1.md"), "```mermaid\nflowchart LR\n a-->b\n```\n")
    const l = [...listed, { path: "docs/architecture/c1.md", kind: "c1", title: "c1" }]
    expect(((await openDiagram(repo, "docs/architecture/c1.md", l)) as any).text).toContain("flowchart")
    expect(PAGE).not.toContain("does not draw")
  })
})

describe("large views open folded", () => {
  test("a view within the budget opens unfolded", () => {
    expect(openFolds(big(2, 8))).toEqual([])
    expect(OPEN_BUDGET).toBeGreaterThanOrEqual(15)
  })
  test("a view over the budget folds its biggest groups until the visible nodes fit", async () => {
    const g = big(4, 12) // 48 nodes
    const f = openFolds(g)
    expect(f.length).toBeGreaterThan(0)
    const left = collapseGraph(g, f)
    expect(left.nodes.length).toBeLessThanOrEqual(OPEN_BUDGET)
    expect(left.nodes.some((n: any) => n.collapsed)).toBe(true)
  })
  test("unequal groups: the biggest fold first and the small one stays open when that is enough", () => {
    const groups = [{ id: "group:big", label: "big", status: "context" }, { id: "group:small", label: "small", status: "context" }]
    const nodes = [...Array.from({ length: 20 }, (_, i) => node("module:c::big::m" + i, "group:big")), ...Array.from({ length: 4 }, (_, i) => node("module:c::small::m" + i, "group:small"))]
    expect(openFolds(base("full", "t", nodes, [], groups))).toEqual(["group:big"])
  })
  test("a big view without groups cannot fold: nothing is named", () => {
    expect(openFolds(base("full", "t", Array.from({ length: 30 }, (_, i) => node("module:c::m" + i)), []))).toEqual([])
  })
  test("openDiagram names the folds, collapsedDiagram lays the folded view out", async () => {
    const repo = mkdtempSync(join(tmpdir(), "vrfold-"))
    mkdirSync(join(repo, "docs/architecture"), { recursive: true })
    const g = big(4, 12)
    writeFileSync(join(repo, "docs/architecture/modules-c.json"), JSON.stringify(g))
    const listed = [{ path: "docs/architecture/modules-c.json", kind: "modules", title: g.title, view: "full" }]
    const d: any = await openDiagram(repo, listed[0].path, listed)
    expect(d.views.L2.collapsed.length).toBeGreaterThan(0)
    const f = await collapsedDiagram(repo, listed[0].path, listed, d.views.L2.collapsed)
    expect(f.graph.nodes.length).toBeLessThanOrEqual(OPEN_BUDGET)
    expect(Object.keys(f.layout.boxes).length).toBeGreaterThanOrEqual(f.graph.nodes.length)
  })
})

describe("aggregated edge labels", () => {
  const E = (kind: string, label?: string) => ({ kind, ...(label ? { label } : {}) })
  test("the common label wins", () => expect(aggLabel([E("depends", "builds on"), E("depends", "builds on"), E("depends", "builds on")])).toBe("builds on ×3"))
  test("no shared label: the shared kind's verb", () => {
    expect(aggLabel([E("depends"), E("depends")])).toBe("builds on ×2")
    expect(aggLabel([E("calls", "6"), E("calls", "2")])).toBe("calls ×2")
  })
  test("mixed kinds and labels: N links", () => expect(aggLabel([E("depends", "a"), E("calls", "b"), E("x")])).toBe("3 links"))
  test("never longer than the limit", () => {
    const long = "x".repeat(70)
    expect(aggLabel([E("k", long), E("k", long)]).length).toBeLessThanOrEqual(EDGE_LABEL_MAX)
  })
  test("collapseGraph labels the aggregate", () => {
    const g = base("L2", "t", [node("n1", "ga"), node("n2", "ga"), node("n3", "gb"), node("n4", "gb")], [edge("n1", "n3", "depends", "uses"), edge("n2", "n4", "depends", "uses")],
      [{ id: "ga", label: "A", status: "context" }, { id: "gb", label: "B", status: "context" }])
    const c = collapseGraph(g, ["ga", "gb"])
    expect(c.edges.map((e: any) => e.label)).toEqual(["uses ×2"])
  })
})

describe("the dense folded product map", () => {
  // 4 areas, every ordered pair joined by edges of different counts: 12 aggregates
  const names = ["a", "b", "c", "d"]
  const groups = names.map((n) => ({ id: "area:" + n, label: n, status: "context" }))
  const nodes: any[] = []
  const edges: any[] = []
  names.forEach((n) => { for (let i = 0; i < 4; i++) nodes.push(node("feature:" + n + i, "area:" + n, "feature")) })
  names.forEach((s, si) => names.forEach((t, ti) => { if (s === t) return; for (let i = 0; i <= (si + ti) % 3 + (si > ti ? 1 : 0); i++) edges.push(edge("feature:" + s + i, "feature:" + t + i, "depends", "builds on")) }))
  const map = base("map", "map", nodes, edges, groups)
  test("opens with one edge per pair, both counts kept", () => {
    const c = collapseGraph(map, names.map((n) => "area:" + n))
    expect(c.nodes).toHaveLength(4)
    expect(c.edges).toHaveLength(6)
    for (const e of c.edges) {
      expect(e).toMatchObject({ bidirectional: true, kind: "aggregate" })
      expect(e.counts).toHaveLength(2)
      expect(e.label).toMatch(/^builds on ×\d+ \| ×\d+$/)
      expect(e.count).toBe(e.counts[0] + e.counts[1])
      expect(e.evidence.length).toBe(Math.min(50, e.count))
    }
    // nothing is lost: the counts sum to the number of edges between folded boxes
    expect(c.edges.reduce((a: number, e: any) => a + e.count, 0)).toBe(edges.length)
  })
  test("a sparse folded graph keeps its directed edges", () => {
    const g = base("L2", "t", [node("n1", "ga"), node("n2", "gb")], [edge("n1", "n2"), edge("n2", "n1")], [{ id: "ga", label: "A", status: "context" }, { id: "gb", label: "B", status: "context" }])
    expect(collapseGraph(g, ["ga", "gb"]).edges).toHaveLength(2)
  })
  test("the page draws counts as width and a merged edge with arrows at both ends", () => {
    expect(PAGE).toContain("e.bidirectional")
    expect(PAGE).toContain("marker-start")
  })
})
