// VR L1 flow order: a flow's before/after step lists become edges, so the layout runs the steps in order instead of one flat strip.
// Run: bun test shared/test/vrflow.test.ts
import { afterAll, expect, test } from "bun:test"
import { closeLayout, layoutGraph, withFlowEdges } from "../questions/vrlayout.ts"

afterAll(() => closeLayout())
const step = (id: string, lane: string, status = "added") => ({ id, label: "Step " + id, kind: "step", status, lane, evidence: [] })
const L1 = (edges: any[] = []) => ({
  schema_version: "1.0.0", view: "L1", lanes: [{ id: "la", label: "A" }, { id: "lb", label: "B" }],
  nodes: [step("s1", "la", "context"), step("s2", "la"), step("s3", "lb"), step("s4", "lb", "removed")],
  edges, flows: [{ id: "f", label: "F", status: "changed", before: ["s1", "s4", "s3"], after: ["s1", "s2", "s3"], evidence: [] }],
})

test("consecutive steps get an edge with added, removed or context status", () => {
  const g = withFlowEdges({ ...L1(), flows: [{ id: "f", before: ["s1", "s2", "s4"], after: ["s1", "s2", "s3"], evidence: [] }] })
  const by = Object.fromEntries(g.edges.map((e: any) => [e.source + ">" + e.target, e.status]))
  expect(by).toEqual({ "s1>s2": "context", "s2>s3": "added", "s2>s4": "removed" })
  expect(g.edges.every((e: any) => e.derived === true)).toBe(true)
})
test("a pair a file edge already joins is not doubled; unknown ids are skipped", () => {
  const g = withFlowEdges({ ...L1([{ id: "e", source: "s1", target: "s2", status: "added", evidence: [] }]), flows: [{ id: "f", before: [], after: ["s1", "s2", "nope", "s3"] }] })
  expect(g.edges.map((e: any) => e.source + ">" + e.target)).toEqual(["s1>s2", "s2>s3"])
})
test("other views and graphs without flows are returned as they are", () => {
  const g = { ...L1(), view: "L2" }
  expect(withFlowEdges(g)).toBe(g)
  const h = { ...L1(), flows: [] }
  expect(withFlowEdges(h)).toBe(h)
})
test("a flow with no edges lays out taller than wide, not as one strip", async () => {
  const L = await layoutGraph(withFlowEdges(L1()))
  expect(L.height).toBeGreaterThan(L.width * 0.6)
  const flat = await layoutGraph(L1())
  expect(flat.width / flat.height).toBeGreaterThan(L.width / L.height)
})
