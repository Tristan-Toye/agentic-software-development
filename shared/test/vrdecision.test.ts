// The decision flow view (view `decision`): decision.json is a fourth tab, laid out top to bottom with the shapes, branch labels on its
// edges, a before/after diff and a Mermaid export; it is never an architecture draft and never promoted. Run: bun test shared/test/vrdecision.test.ts
import { afterAll, describe, expect, test } from "bun:test"
import { cpSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { dirname, join } from "node:path"
import { ARCH_VIEWS, checkGraph, closeLayout, DECISION_VIEW, elkInput, hasVisualReview, isDecisionView, isLaneView, reviewViews, tabViews, viewFile, visualReview, vrDir } from "../questions/vrlayout.ts"
import { approveAll, buildAnswer, readGraphs, reviewState, setVerdict } from "../questions/vrreview.ts"
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
    expect(reviewViews(none)).toEqual(["L0", "L1", "L2"]) // no decision.json: the review is what it was
    expect(reviewViews(dir)).toEqual(["L0", "L1", DECISION_VIEW, "L2"]) // decision.json: it takes verdicts like the others
    expect(viewFile(dir, "decision")).toBe(join(dir, "decision.json"))
  })
  test("hasVisualReview is true for a dossier with only decision.json", () => {
    const root = mkdtempSync(join(tmpdir(), "vr-has-"))
    const d = vrDir(root, "W-1")
    mkdirSync(d, { recursive: true })
    expect(hasVisualReview(root, "W-1")).toBe(false)
    cpSync(join(FIX, "decision-retry.json"), join(d, "decision.json"))
    expect(hasVisualReview(root, "W-1")).toBe(true)
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

describe("the decision flow takes the owner's verdicts when decision.json exists", () => {
  const withDecision = () => dossier({ "l0.json": "w145-l0.json", "l1.json": "w145-l1.json", "l2.json": "w145-l2.json", "decision.json": "decision-retry.json" })
  const without = () => dossier({ "l0.json": "w145-l0.json", "l1.json": "w145-l1.json", "l2.json": "w145-l2.json" })
  const decIds = (): string[] => load("decision-retry.json").nodes.map((n: any) => n.id)
  const status = (f: () => unknown): number => { try { f() } catch (e: any) { return e.status } return 0 }

  test("its nodes are elements of the review: unchecked until looked at, so the review is not Approved", () => {
    const d = withDecision()
    expect(Object.keys(readGraphs(d))).toContain("decision")
    const st = reviewState(d, "W-1")
    expect(st.unchecked.filter((u) => u.diagram === "decision").map((u) => u.element).sort()).toEqual(decIds().sort())
    expect(buildAnswer(st, "W-1").option).toBe("B")
    expect(buildAnswer(st, "W-1").text).toContain("decision " + decIds()[0])
  })
  test("a verdict on a decision node is stored with the anchor decision, a Question goes to the agent as a follow-up", () => {
    const d = withDecision()
    const r = setVerdict(d, "W-1", { diagram: "decision", element: decIds()[0], verdict: "question", comment: "why here?" })
    expect(r.state.lines[0].anchor.diagram).toBe("decision")
    expect(r.followup).not.toBeNull()
    expect(reviewState(d, "W-1").hashes.decision).toMatch(/^[0-9a-f]{64}$/)
  })
  test("a verdict on decision is refused when there is no decision.json", () => {
    expect(status(() => setVerdict(without(), "W-1", { diagram: "decision", element: "x", verdict: "ok" }))).toBeGreaterThanOrEqual(400)
  })
  test("Approve all marks the decision nodes OK too; a flagged decision node refuses it; the answer is A only with all of them OK", () => {
    const d = withDecision()
    setVerdict(d, "W-1", { diagram: "decision", element: decIds()[1], verdict: "change", comment: "wrong arm" })
    expect(status(() => approveAll(d, "W-1"))).toBe(409)
    setVerdict(d, "W-1", { diagram: "decision", element: decIds()[1], verdict: "ok" })
    const st = approveAll(d, "W-1")
    expect(st.lines.filter((l) => l.anchor.diagram === "decision")).toHaveLength(decIds().length)
    expect(st.summary.unchecked).toBe(0)
    expect(buildAnswer(st, "W-1").option).toBe("A")
  })
  test("without decision.json the review is what it was: approving L0, L1 and L2 alone answers A, and no decision line appears", () => {
    const st = approveAll(without(), "W-1")
    expect(st.lines.some((l) => l.anchor.diagram === "decision")).toBe(false)
    expect(buildAnswer(st, "W-1").option).toBe("A")
  })
  test("approving the three files, then adding decision.json, leaves the answer B until the decision flow is looked at", () => {
    const d = without()
    expect(buildAnswer(approveAll(d, "W-1"), "W-1").option).toBe("A")
    cpSync(join(FIX, "decision-retry.json"), join(d, "decision.json"))
    expect(buildAnswer(reviewState(d, "W-1"), "W-1").option).toBe("B")
  })
  test("the page names the Decisions tab and the follow-up anchor accepts decision", () => {
    expect(PAGE).toContain("decision: 'Decisions'")
    expect(readFileSync(join(import.meta.dir, "..", "questions", "server.ts"), "utf8")).toContain('["L0", "L1", "L2", "decision"].includes(a.diagram)')
  })
  test("it is never promoted: ARCH_VIEWS excludes decision and decision.json is no architecture stem", () => {
    expect(ARCH_VIEWS.has("decision")).toBe(false)
    expect(reviewViews(withDecision()).filter((v) => v.startsWith("arch:"))).toEqual([])
  })
})
