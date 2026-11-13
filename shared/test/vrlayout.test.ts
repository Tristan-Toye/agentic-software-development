// VR-03: server-side ELK layout (vrlayout.ts), the /api/visual-review route and the page's canvas code.
// Run: bun test shared/test/vrlayout.test.ts
import { afterAll, describe, expect, test } from "bun:test"
import { cpSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { checkGraph, closeLayout, FAST_ABOVE, graphHash, layoutCacheSize, layoutGraph, stripDollar, visualReview } from "../questions/vrlayout.ts"
import { PAGE } from "../questions/page.ts"

const FIX = join(import.meta.dir, "..", "..", "references", "visual-review", "fixtures")
const load = (n: string) => JSON.parse(readFileSync(join(FIX, n), "utf8"))
afterAll(() => closeLayout())

describe("layout of the W-145 fixtures", () => {
  for (const v of ["l0", "l1", "l2"]) {
    test(v + ": every node, group and lane has a box inside the canvas, every edge a path", async () => {
      const g = load(`w145-${v}.json`)
      const L = await layoutGraph(g)
      const ids = [...g.nodes, ...(g.groups ?? []), ...(g.view === "L1" ? g.lanes ?? [] : [])].map((x: any) => x.id)
      for (const id of ids) {
        const b = L.boxes[id]
        expect(b, id).toBeDefined()
        expect(b.w).toBeGreaterThan(0)
        expect(b.x + b.w).toBeLessThanOrEqual(L.width + 1)
        expect(b.y + b.h).toBeLessThanOrEqual(L.height + 1)
      }
      for (const e of g.edges) expect(L.edges[e.id]?.length, e.id).toBeGreaterThanOrEqual(2)
      expect(L.direction).toBe(g.view === "L1" ? "DOWN" : "RIGHT")
      expect(L.mode).toBe("thorough")
    })
  }
  test("a group box holds its members", async () => {
    const g = load("w145-l2.json"), L = await layoutGraph(g)
    for (const n of g.nodes.filter((x: any) => x.group)) {
      const o = L.boxes[n.group], b = L.boxes[n.id]
      expect(b.x >= o.x && b.y >= o.y && b.x + b.w <= o.x + o.w && b.y + b.h <= o.y + o.h, n.id).toBe(true)
    }
  })
  test("removed.json (removed elements) and minimal.json lay out", async () => {
    for (const f of ["removed.json", "minimal.json"]) {
      const L = await layoutGraph(load(f))
      expect(Object.keys(L.boxes).length).toBeGreaterThan(0)
    }
  })
})

describe("determinism, cache and $-keys", () => {
  test("two runs give byte-identical output, with and without the cache", async () => {
    const g = load("w145-l2.json")
    const a = JSON.stringify(await layoutGraph(g))
    closeLayout()
    const g2 = JSON.parse(JSON.stringify(g))
    g2.$comment = "noise"; g2.nodes[0].$scratch = { x: 1 }
    const b = JSON.stringify(await layoutGraph(g2))
    expect(b).toBe(a)
  })
  test("a fresh layout without the cache is identical too", async () => {
    const g = load("w145-l1.json")
    const a = JSON.stringify(await layoutGraph(g))
    // a different title is a cache miss (the hash covers the whole graph) but must not move anything
    const g2 = { ...g, title: g.title + " " }
    const b = JSON.stringify(await layoutGraph(g2))
    expect(b).toBe(a)
  })
  test("the hash ignores $-keys and key order, and sees real changes", () => {
    const g = load("w145-l0.json")
    expect(graphHash({ ...g, $x: 1 })).toBe(graphHash(g))
    expect(graphHash(Object.fromEntries(Object.entries(g).reverse()))).toBe(graphHash(g))
    expect(graphHash({ ...g, title: "other" })).not.toBe(graphHash(g))
    expect(stripDollar({ $a: 1, b: [{ $c: 2, d: 3 }] })).toEqual({ b: [{ d: 3 }] })
  })
  test("the cache serves the second call", async () => {
    const g = load("w145-l0.json")
    await layoutGraph(g)
    const n = layoutCacheSize()
    await layoutGraph(g)
    expect(layoutCacheSize()).toBe(n)
  })
  test("a graph with a parent cycle among groups still lays out", async () => {
    const g = load("w145-l2.json")
    g.groups[0].parent = g.groups[1].id
    g.groups[1].parent = g.groups[0].id
    const L = await layoutGraph(g)
    expect(L.boxes[g.groups[0].id]).toBeDefined()
  })
})

describe("timeout and the worker", () => {
  test("a timeout rejects and the next layout still works", async () => {
    const g = synth(120, 0)
    await expect(layoutGraph(g, 1)).rejects.toThrow(/timed out/)
    const L = await layoutGraph(load("w145-l0.json"))
    expect(Object.keys(L.boxes).length).toBeGreaterThan(0)
  })
  test("the process exits after a layout (no hang)", async () => {
    const script = `import { layoutGraph } from ${JSON.stringify(join(import.meta.dir, "..", "questions", "vrlayout.ts"))}
const g = JSON.parse(await Bun.file(${JSON.stringify(join(FIX, "w145-l2.json"))}).text())
const L = await layoutGraph(g)
console.log("laid out", Object.keys(L.boxes).length)`
    const t = Date.now()
    const p = Bun.spawn([process.execPath, "--no-install", "-e", script], { stdout: "pipe", stderr: "pipe" })
    const timer = setTimeout(() => p.kill(), 15_000)
    const out = await new Response(p.stdout).text()
    const code = await p.exited
    clearTimeout(timer)
    expect(out).toContain("laid out")
    expect(code).toBe(0)
    expect(Date.now() - t).toBeLessThan(14_000)
  })
  test("closeLayout is idempotent", () => { closeLayout(); closeLayout() })
})

/** N synthetic nodes in groups of 10, two edges each (like the D0 benchmark). */
function synth(n: number, nGroups = Math.ceil(n / 10)): any {
  const nodes: any[] = [], edges: any[] = [], groups: any[] = []
  for (let g = 0; g < nGroups; g++) groups.push({ id: "g" + g, label: "group " + g, status: "context" })
  for (let i = 0; i < n; i++) nodes.push({ id: "n" + i, label: "node number " + i, kind: "module", status: (["added", "changed", "removed", "context"] as const)[i % 4], evidence: [{ tier: "inferred", note: "synthetic" }], ...(nGroups ? { group: "g" + Math.floor(i / 10) } : {}) })
  for (let i = 1; i < n; i++) { edges.push({ id: "e" + i, source: "n" + (i - 1), target: "n" + i, kind: "calls", status: "context", evidence: [] }); if (i > 3) edges.push({ id: "f" + i, source: "n" + Math.floor(i / 2), target: "n" + i, kind: "calls", status: "context", evidence: [] }) }
  return { schema_version: "1.0.0", view: "full", title: "synthetic " + n, meta: { repo: "x", commit: "0", generated_by: "hand" }, nodes, edges, groups }
}
describe("timing", () => {
  test("200 synthetic nodes lay out within a generous bound, in the cheap mode above the limit", async () => {
    const t = Date.now()
    const L = await layoutGraph(synth(200))
    const ms = Date.now() - t
    console.log("200 nodes:", ms, "ms, mode", L.mode)
    expect(ms).toBeLessThan(10_000)
    expect(Object.keys(L.boxes).length).toBe(200 + 20)
    expect(200).toBeGreaterThan(FAST_ABOVE)
    expect(L.mode).toBe("fast")
  })
  test("100 nodes (VC-06) lay out within 5 s", async () => {
    const t = Date.now()
    const L = await layoutGraph(synth(100))
    expect(Date.now() - t).toBeLessThan(5_000)
    expect(L.mode).toBe("thorough")
  })
})

describe("validation", () => {
  test("the W-145 fixtures are clean", () => { for (const v of ["l0", "l1", "l2"]) expect(checkGraph(load(`w145-${v}.json`), v.toUpperCase())).toEqual([]) })
  test("the problems the reader depends on are named", () => {
    const g = load("w145-l0.json")
    expect(checkGraph({ ...g, schema_version: "2.0.0" }).map((i) => i.code)).toContain("schema_version")
    expect(checkGraph(g, "L2").map((i) => i.code)).toContain("view_mismatch")
    expect(checkGraph({ ...g, nodes: [...g.nodes, g.nodes[0]] }).map((i) => i.code)).toContain("duplicate_id")
    expect(checkGraph({ ...g, edges: [{ ...g.edges[0], target: "ghost" }] }).map((i) => i.code)).toContain("dangling_edge")
    expect(checkGraph({ ...g, nodes: [{ ...g.nodes[0], status: "weird" }] }).map((i) => i.code)).toContain("status")
    expect(checkGraph(null).map((i) => i.code)).toEqual(["not_object"])
  })
})

describe("visualReview: the three files", () => {
  const dir = mkdtempSync(join(tmpdir(), "vr-dir-"))
  for (const v of ["l0", "l1", "l2"]) cpSync(join(FIX, `w145-${v}.json`), join(dir, `${v}.json`))
  test("all three views come back with layouts, no issues", async () => {
    const r = await visualReview(dir, "W-145")
    expect(r.issues).toEqual([])
    for (const v of ["L0", "L1", "L2"] as const) expect(r.views[v]?.layout?.boxes).toBeDefined()
    expect(r.rev).toMatch(/^[0-9a-f]{16}$/)
  })
  test("a review.json next to them (VR-04) breaks nothing", async () => {
    writeFileSync(join(dir, "review.json"), JSON.stringify({ anything: ["goes"] }))
    const r = await visualReview(dir, "W-145")
    expect(r.hasReview).toBe(true)
    expect(r.issues).toEqual([])
  })
  test("a broken file reports its issue and the others still lay out; a dangling edge is dropped, not fatal", async () => {
    const d2 = mkdtempSync(join(tmpdir(), "vr-bad-"))
    writeFileSync(join(d2, "l0.json"), "{ not json")
    const l1 = load("w145-l1.json"); l1.edges.push({ id: "ghost", source: "s1", target: "nowhere", kind: "calls", status: "added", evidence: [] })
    writeFileSync(join(d2, "l1.json"), JSON.stringify(l1))
    const r = await visualReview(d2, "W-145")
    expect(r.views.L0?.layout).toBeNull()
    expect(r.views.L0?.issues[0].code).toBe("unreadable")
    expect(r.views.L1?.layout).not.toBeNull()
    expect(r.views.L1?.issues.map((i) => i.code)).toEqual(["dangling_edge"])
    expect(r.views.L1?.graph.edges.some((e: any) => e.id === "ghost")).toBe(false)
    expect(r.views.L2).toBeNull()
  })
})

describe("GET /api/visual-review", () => {
  const root = mkdtempSync(join(tmpdir(), "vr-route-"))
  const repo = join(root, "repoV")
  mkdirSync(join(repo, ".discovery", "dossiers"), { recursive: true })
  for (const id of ["W-145", "W-146"]) writeFileSync(join(repo, ".discovery", "dossiers", id + ".md"), `---\nid: ${id}\ntitle: T ${id}\nstatus: ready\n---\nbody\n`)
  const vdir = join(repo, ".discovery", "visual-review", "W-145")
  mkdirSync(vdir, { recursive: true })
  for (const v of ["l0", "l1", "l2"]) cpSync(join(FIX, `w145-${v}.json`), join(vdir, `${v}.json`))
  const cdir = mkdtempSync(join(tmpdir(), "vr-route-x-"))
  let proc: ReturnType<typeof Bun.spawn> | undefined
  let base = ""
  async function start(): Promise<void> {
    if (base) return
    proc = Bun.spawn([process.execPath, "--no-install", join(import.meta.dir, "..", "questions", "server.ts")], {
      env: { ...process.env, ASD_QUESTIONS_DIR: join(cdir, "q"), ASD_QUESTIONS_PORT: "0", ASD_QUESTIONS_NO_NOTIFY: "1", ASD_DISK_WATCH: "0", ASD_LAG_WATCH: "0", ASD_QUESTIONS_NO_SLACK: "1", ASD_SLACK_USER: "", ASD_RUNS_ROOTS: repo, ASD_OPEN_WORK_DIR: join(cdir, "ow"), ASD_RUNS_DIR: join(cdir, "runs"), ASD_CLAUDE_PROJECTS: join(cdir, "projects"), ASD_RUNS_NO_GH: "1", ASD_BLOCK_LOG: join(cdir, "blocks.jsonl"), ASD_CHAT_DIR: join(cdir, "chats"), ASD_DM_AGENT_FILE: join(cdir, "dm.json") },
      stdout: "pipe", stderr: "ignore",
    })
    const reader = (proc.stdout as ReadableStream).getReader()
    let out = ""
    while (!/listening http:\/\/127\.0\.0\.1:(\d+)/.test(out)) {
      const { value, done } = await reader.read()
      if (done) break
      out += new TextDecoder().decode(value)
    }
    base = out.match(/listening (http:\/\/127\.0\.0\.1:\d+)/)?.[1] ?? ""
  }
  afterAll(() => { proc?.kill() })

  test("200: a dossier with the files gets a layout per view", async () => {
    await start()
    expect(base).not.toBe("")
    const r = await fetch(`${base}/api/visual-review?dossier=W-145`)
    expect(r.status).toBe(200)
    const j: any = await r.json()
    expect(j.dossier).toBe("W-145")
    expect(j.issues).toEqual([])
    for (const v of ["L0", "L1", "L2"]) { expect(j.views[v].layout.boxes).toBeDefined(); expect(j.views[v].graph.nodes.length).toBeGreaterThan(0) }
    const r2 = await fetch(`${base}/api/visual-review?dossier=W-145&repo=repoV`)
    expect(r2.status).toBe(200)
    expect(((await r2.json()) as any).rev).toBe(j.rev)
  })
  test("404 with a clear message: a dossier without a visual-review folder", async () => {
    await start()
    const r = await fetch(`${base}/api/visual-review?dossier=W-146`)
    expect(r.status).toBe(404)
    expect(((await r.json()) as any).error).toMatch(/no visual review for W-146/)
  })
  test("404 for an unknown dossier, 400 for a malformed one", async () => {
    await start()
    expect((await fetch(`${base}/api/visual-review?dossier=W-999`)).status).toBe(404)
    expect((await fetch(`${base}/api/visual-review?dossier=W-145&repo=nope`)).status).toBe(404)
    expect((await fetch(`${base}/api/visual-review?dossier=${encodeURIComponent("../etc")}`)).status).toBe(400)
    expect((await fetch(`${base}/api/visual-review`)).status).toBe(400)
  })
  test("the vendored worker is served from the dashboard's own origin", async () => {
    await start()
    const r = await fetch(`${base}/vendor/elk/elk-worker.min.js`)
    expect(r.status).toBe(200)
    expect((await r.arrayBuffer()).byteLength).toBe(1631868)
  })
  test("the page keeps its promises: no external URL, no markup parsing on the diagram path", () => {
    expect(PAGE).not.toMatch(/https?:\/\//)
    const code = PAGE.slice(PAGE.indexOf("// ---- visual review (VR-03)"), PAGE.indexOf("// ---- state ----"))
    expect(code.length).toBeGreaterThan(1000)
    expect(code).not.toMatch(/innerHTML|outerHTML|insertAdjacentHTML|document\.write/)
    expect(code).not.toMatch(/[`]/)
    expect(code).not.toContain("${")
  })
})
