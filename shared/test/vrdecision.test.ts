// The decision flow view (view `decision`): decision.json is a fourth tab, laid out top to bottom with the shapes, branch labels on its
// edges, a before/after diff and a Mermaid export; it is never an architecture draft and never promoted. Run: bun test shared/test/vrdecision.test.ts
import { afterAll, describe, expect, test } from "bun:test"
import { cpSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import { ARCH_VIEWS, checkGraph, closeLayout, DECISION_VIEW, elkInput, isDecisionView, isLaneView, reviewViews, tabViews, viewFile, visualReview } from "../questions/vrlayout.ts"
import { vrDecisionStats } from "../questions/vrdecision.ts"
import { vrWalkOrder } from "../questions/vrwalk.ts"
import { PAGE } from "../questions/page.ts"

afterAll(() => closeLayout())

const FIX = join(import.meta.dir, "..", "..", "references", "visual-review", "fixtures")
const load = (f: string): any => JSON.parse(readFileSync(join(FIX, f), "utf8"))

function dossier(files: Record<string, string>): string {
  const dir = mkdtempSync(join(tmpdir(), "vr-decision-"))
  for (const [name, fix] of Object.entries(files)) {
    mkdirSync(dirname(join(dir, name)), { recursive: true })
    cpSync(join(FIX, fix), join(dir, name))
  }
  return dir
}

describe("the decision view is a known view", () => {
  test("checkGraph accepts view decision, and demands it when asked", () => {
    expect(checkGraph(load("decision-retry.json"), "decision")).toEqual([])
    expect(checkGraph(load("decision-retry.json"), "L2").map((i) => i.code)).toEqual(["view_mismatch"])
  })
  test("it reads top to bottom with no lanes, and the happy path gets a straightness priority", () => {
    expect(isDecisionView("decision")).toBe(true)
    expect(isLaneView("decision")).toBe(false)
    const { direction, root } = elkInput(load("decision-retry.json"), "thorough")
    expect(direction).toBe("DOWN")
    const prio = root.edges.filter((e: any) => e.layoutOptions?.["elk.layered.priority.straightness"]).map((e: any) => e.id)
    expect(prio.some((id: string) => id.endsWith(":end:ok:delivered"))).toBe(true) // a yes exit
    expect(prio.some((id: string) => id.includes("attempts-left:proc:deliver::dead_letter"))).toBe(false) // a no exit bends away
  })
})

describe("decision.json on the dossier page", () => {
  test("it is a tab between L1 and L2 only when the file exists, and never one of the reviewed diagrams", () => {
    const none = dossier({ "l0.json": "w145-l0.json", "l1.json": "w145-l1.json", "l2.json": "w145-l2.json" })
    expect(tabViews(none)).toEqual(["L0", "L1", "L2"])
    const dir = dossier({ "l0.json": "w145-l0.json", "l1.json": "w145-l1.json", "l2.json": "w145-l2.json", "decision.json": "decision-retry.json" })
    expect(tabViews(dir)).toEqual(["L0", "L1", DECISION_VIEW, "L2"])
    expect(reviewViews(dir)).toEqual(["L0", "L1", "L2"]) // the verdict gate is a separate change
    expect(viewFile(dir, "decision")).toBe(join(dir, "decision.json"))
  })
  test("visualReview lays it out with shapes, branch labels and no layout issue", async () => {
    const dir = dossier({ "l0.json": "w145-l0.json", "decision.json": "decision-retry-diff.json" })
    const r = await visualReview(dir, "W-1")
    const v: any = r.views.decision
    expect(v.issues).toEqual([])
    expect(v.layout.direction).toBe("DOWN")
    expect(v.layout.shapes["end:start:delivery"]).toBe("terminal")
    expect(v.layout.shapes["decision:deliver::run:status-ok"]).toBe("diamond")
    expect(v.layout.shapes["proc:deliver::send"]).toBe("io")
    const labelled = v.graph.edges.filter((e: any) => e.label)
    expect(labelled.length).toBeGreaterThan(3)
    for (const e of labelled) expect(v.layout.edges[e.id]).toBeTruthy()
    // a decision reads down: the start sits above every other node
    const y = (id: string) => v.layout.boxes[id].y
    expect(Object.keys(v.layout.boxes).filter((id) => id !== "end:start:delivery").every((id) => y(id) > y("end:start:delivery"))).toBe(true)
    // the walkthrough steps through the changes in reading order, the added decision before the node it leads to
    const order = vrWalkOrder(v.graph, v.layout)
    expect(order.indexOf("decision:deliver::run:attempts-left")).toBeLessThan(order.indexOf("end:error:parked"))
  })
  test("a broken decision file gives its issues and no layout, and does not hide the other tabs", async () => {
    const dir = dossier({ "l0.json": "w145-l0.json", "decision.json": "decision-retry.json" })
    const g = load("decision-retry.json")
    delete g.nodes
    writeFileSync(join(dir, "decision.json"), JSON.stringify(g))
    const r = await visualReview(dir, "W-1")
    expect((r.views.decision as any).layout).toBeNull()
    expect((r.views.L0 as any).layout).not.toBeNull()
  })
  test("a decision graph is never accepted as an architecture draft (those are the only diagrams promoted to docs/architecture)", async () => {
    expect(ARCH_VIEWS.has("decision")).toBe(false)
    const dir = dossier({ "l0.json": "w145-l0.json", "arch/c1-x.json": "decision-retry.json" })
    const r = await visualReview(dir, "W-1")
    expect((r.views["arch:c1-x"] as any).layout).toBeNull()
    expect((r.views["arch:c1-x"] as any).issues.map((i: any) => i.code)).toContain("view")
  })
})

describe("the diff of two decision graphs", () => {
  test("the stats count decisions, paths and new error ends of each side, the same numbers vr_diff.py writes", () => {
    const m = load("decision-retry-diff.json")
    const s = vrDecisionStats(m)
    expect(s).toEqual({ decisions: [1, 2], paths: [2, 2], errorEnds: 1 })
    expect(m.meta.diff.decision).toEqual({ decisions: s.decisions, paths: s.paths, new_error_ends: s.errorEnds })
    expect(m.meta.diff.summary).toBe("8 added, 1 changed, 2 removed, decisions 1 -> 2, paths 2 -> 2, new error ends 1")
  })
  test("a partial graph never throws", () => {
    expect(vrDecisionStats(null).paths).toEqual([0, 0])
    expect(vrDecisionStats({ nodes: [{ id: "a", status: "added" }], edges: [{ source: "a", target: "zz" }] }).decisions).toEqual([0, 0])
  })
  test("the page ships the stats function, the Decisions tab and the top-down Mermaid direction", () => {
    expect(PAGE).toContain("function vrDecisionStats")
    expect(PAGE).toContain("decision: 'Decisions'")
    expect(PAGE).toContain("vrDown(g.view) ? 'TD' : 'LR'")
  })
})
