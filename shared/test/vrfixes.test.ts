// Review fixes for the visual-review dashboard code: diagram list cache key, kept failure message, open status codes,
// dangling edges with spaces in their ids, and a title-less graph.
import { afterAll, describe, expect, test } from "bun:test"
import { appendFileSync, mkdirSync, mkdtempSync, readFileSync, utimesSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { checkGraph, visualReview } from "../questions/vrlayout.ts"
import { clearMapCache, DiagramError, mapStatus, openDiagram } from "../questions/vrmap.ts"
import { PAGE } from "../questions/page.ts"

const tmp = mkdtempSync(join(tmpdir(), "vrfix-"))
const prevBin = process.env.ASD_VR_MAP_BIN
afterAll(() => { if (prevBin === undefined) delete process.env.ASD_VR_MAP_BIN; else process.env.ASD_VR_MAP_BIN = prevBin; clearMapCache() })

const node = (id: string) => ({ id, label: id, status: "context", kind: "x", evidence: [] })
const graph = (view: string, extra: any = {}) => ({ schema_version: "1.0.0", view, title: "T", meta: {}, nodes: [node("a"), node("b")], edges: [], ...extra })

describe("mapStatus", () => {
  const fake = (name: string, body: string) => { const f = join(tmp, name); writeFileSync(f, body); process.env.ASD_VR_MAP_BIN = f; return f }
  test("a diagram file added to docs/architecture is seen at once, not after the 60 s cache", async () => {
    const repo = mkdtempSync(join(tmp, "repo-")), arch = join(repo, "docs", "architecture")
    mkdirSync(arch, { recursive: true })
    writeFileSync(join(arch, "product-map.json"), "{}")
    const log = join(tmp, "calls.log")
    fake("list.py", `import sys, json, os\nopen(${JSON.stringify(log)}, "a").write(sys.argv[1] + "\\n")\nif sys.argv[1] == "list":\n    print(json.dumps({"has_map": True, "diagrams": [{"path": "docs/architecture/" + f, "kind": "x", "title": f} for f in sorted(os.listdir(${JSON.stringify(arch)}))]}))\nelse:\n    print(json.dumps({"stale": [], "banner": ""}))\n`)
    clearMapCache()
    expect((await mapStatus("r", repo)).diagrams.length).toBe(1)
    writeFileSync(join(arch, "new.json"), "{}")
    const t = new Date(Date.now() + 5000)
    utimesSync(arch, t, t)
    expect((await mapStatus("r", repo)).diagrams.length).toBe(2)
  })
  test("a failed or timed out list keeps its message when there is no map", async () => {
    const repo = mkdtempSync(join(tmp, "repo-"))
    fake("slow.py", "import time\ntime.sleep(30)\n")
    clearMapCache()
    const m = await mapStatus("r", repo, 400)
    expect(m.message).toContain("vr_map list timed out")
    expect(m.message).toContain("no committed product map")
    fake("fail.py", "import sys\nsys.stderr.write('boom\\n')\nsys.exit(3)\n")
    clearMapCache()
    expect((await mapStatus("r", repo)).message).toContain("vr_map list failed: boom")
  })
})

describe("openDiagram status codes", () => {
  const repo = mkdtempSync(join(tmp, "open-"))
  mkdirSync(join(repo, "docs", "architecture"), { recursive: true })
  writeFileSync(join(repo, "docs/architecture/bad.json"), "{ not json")
  writeFileSync(join(repo, "docs/architecture/noview.json"), JSON.stringify({ schema_version: "1.0.0", title: "T", nodes: [], edges: [] }))
  const listed = ["bad.json", "noview.json", "gone.json"].map((f) => ({ path: "docs/architecture/" + f, kind: "x", title: f }))
  const status = async (p: string, l = listed) => { try { await openDiagram(repo, p, l); return 200 } catch (e) { return e instanceof DiagramError ? e.status : -1 } }
  test("404 for an unlisted or missing diagram, 400 for bad JSON", async () => {
    expect(await status("docs/architecture/other.json")).toBe(404)
    expect(await status("docs/architecture/gone.json")).toBe(404)
    expect(await status("docs/architecture/bad.json")).toBe(400)
  })
  test("a file with no view opens with issues rather than an error", async () => {
    const d: any = await openDiagram(repo, "docs/architecture/noview.json", listed)
    expect(d.views[Object.keys(d.views)[0]].issues.map((i: any) => i.code)).toContain("view")
  })
})

describe("graphs", () => {
  test("a graph without a title is an issue", () => {
    const { title, ...g } = graph("L1") as any
    expect(checkGraph(g).map((i) => i.where)).toContain("title")
    expect(checkGraph(graph("L1"))).toEqual([])
  })
  test("dangling edges are dropped by id sets, also when the ids hold spaces", async () => {
    const d = mkdtempSync(join(tmp, "dang-"))
    const e = (id: string, target: string) => ({ id, source: "a", target, kind: "calls", status: "context", evidence: [] })
    writeFileSync(join(d, "l1.json"), JSON.stringify(graph("L1", { edges: [e("x", "b"), e("x y", "ghost"), e("x y z", "b")] })))
    const r = await visualReview(d, "W-1")
    expect(r.views.L1?.issues.map((i) => i.code)).toEqual(["dangling_edge"])
    expect(r.views.L1?.graph.edges.map((x: any) => x.id)).toEqual(["x", "x y z"])
  })
})

describe("page code", () => {
  test("the page sequences diagram opens, sends the seen hash and says why Send is off", () => {
    expect(PAGE).toContain("vrOpenSeq")
    expect(PAGE).toContain("graph_hash")
    expect(PAGE).toContain("Send review is enabled when the run asks for the visual review")
    expect(PAGE).toContain("click a folded box to open it")
  })
})
