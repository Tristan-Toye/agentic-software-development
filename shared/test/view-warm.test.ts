import { afterAll, beforeAll, expect, test } from "bun:test"
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

// Warm start of the dashboard: a restart serves the persisted view at once (marked refreshing) while one shared build
// runs, and no build or git/gh call holds the event loop. Everything lives in temp dirs; nothing touches ~/.agentic.
const KEYS = ["ASD_RUNS_ROOTS", "ASD_CLAUDE_PROJECTS", "ASD_RUNS_DIR", "ASD_RUNS_NO_GH", "ASD_VIEW_CACHE_DIR", "ASD_STUBS_NO_GIT"]
const saved: Record<string, string | undefined> = {}
let dir: string
let seeded: any
beforeAll(async () => {
  dir = mkdtempSync(join(tmpdir(), "asd-view-warm-"))
  const root = join(dir, "repo")
  mkdirSync(join(root, ".discovery", "dossiers"), { recursive: true })
  writeFileSync(join(root, ".discovery", "dossiers", "W-1.md"), "---\nid: W-1\ntitle: One\nstatus: ready\n---\n\n## Problem\nx\n")
  mkdirSync(join(dir, "projects")); mkdirSync(join(dir, "runs")); mkdirSync(join(dir, "vc"))
  for (const k of KEYS) saved[k] = process.env[k]
  Object.assign(process.env, { ASD_RUNS_ROOTS: root, ASD_CLAUDE_PROJECTS: join(dir, "projects"), ASD_RUNS_DIR: join(dir, "runs"), ASD_RUNS_NO_GH: "1", ASD_VIEW_CACHE_DIR: join(dir, "vc"), ASD_STUBS_NO_GIT: "1" })
  // The "last session's" view on disk: a real scan of the fixture, written where the server reads it back.
  const { buildRuns } = await import("../questions/runs.ts")
  seeded = await buildRuns([])
  writeFileSync(join(dir, "vc", "runs.json"), JSON.stringify(seeded))
})
afterAll(() => {
  for (const k of KEYS) { if (saved[k] === undefined) delete process.env[k]; else process.env[k] = saved[k] }
  rmSync(dir, { recursive: true, force: true })
})

/** A module instance of runs.ts with fresh state, as a restarted server has. */
const freshRuns = (tag: string) => import(`../questions/runs.ts?${tag}`) as Promise<typeof import("../questions/runs.ts")>
const gate = () => { let open!: () => void; const p = new Promise<void>((r) => { open = r }); return { p, open } }

test("startup serves the persisted view without waiting for the build, marked refreshing", async () => {
  const { getRuns } = await freshRuns("warm")
  const g = gate()
  let built = 0
  const build = async (_q: any) => { built++; await g.p; return { ...seeded, scannedAt: "later" } }
  const t0 = Date.now()
  const v = await getRuns(() => [], { build })
  expect(Date.now() - t0).toBeLessThan(500)
  expect(v.runs.map((r) => r.id)).toEqual(["W-1"])
  expect(v.refreshing).toBe(true)
  expect(built).toBe(1)
  g.open()
  const done = await getRuns(() => [], { fresh: true, build })
  expect(done.refreshing).toBe(false)
  expect(done.scannedAt).toBe("later")
  expect(built).toBe(1)
})

test("concurrent requests during a build share one build", async () => {
  const { getRuns } = await freshRuns("coalesce")
  const g = gate()
  let built = 0
  const build = async (_q: any) => { built++; await g.p; return seeded }
  const calls = Promise.all([1, 2, 3, 4, 5].map((i) => getRuns(() => [], { fresh: i % 2 === 0, build })))
  await Bun.sleep(50)
  g.open()
  const vs = await calls
  expect(built).toBe(1)
  expect(vs.length).toBe(5)
})

test("an action (fresh) never reads the disk copy: it waits for the first live scan", async () => {
  const { getRuns } = await freshRuns("fresh")
  const g = gate()
  const build = async (_q: any) => { await g.p; return { ...seeded, scannedAt: "live" } }
  let got = ""
  const p = getRuns(() => [], { fresh: true, build }).then((v) => { got = v.scannedAt })
  await Bun.sleep(100)
  expect(got).toBe("")
  g.open(); await p
  expect(got).toBe("live")
})

test("a subprocess call does not hold the event loop (shRun), unlike spawnSync", async () => {
  const { shRun } = await freshRuns("sh")
  let maxGap = 0, last = Date.now()
  const timer = setInterval(() => { const n = Date.now(); maxGap = Math.max(maxGap, n - last); last = n }, 20)
  const r = await shRun(["sh", "-c", "sleep 1; echo hi"], undefined, 10_000)
  clearInterval(timer)
  expect(r.code).toBe(0)
  expect(r.stdout.trim()).toBe("hi")
  expect(maxGap).toBeLessThan(300)
  expect((await shRun(["sh", "-c", "sleep 5"], undefined, 100)).code).toBe(124)
})

test("a question answer is not blocked by a slow merge call in the real server", async () => {
  const qdir = join(dir, "q"); mkdirSync(qdir)
  writeFileSync(join(qdir, "repo.W-1.Q1.jsonl"), JSON.stringify({ ts: "2026-01-01T00:00:00Z", from: "agent", kind: "question", text: "Which?", options: [{ key: "A", label: "one", consequence: "c" }] }) + "\n")
  const slow = join(dir, "slow-merge.py")
  writeFileSync(slow, "import time\ntime.sleep(3)\nprint('merge_dossier: would merge')\n")
  const port = 17300 + Math.floor(Math.random() * 500)
  const proc = Bun.spawn(["bun", join(import.meta.dir, "..", "questions", "server.ts")], {
    stdout: "ignore", stderr: "ignore", stdin: "ignore",
    env: { ...process.env, ASD_QUESTIONS_PORT: String(port), ASD_QUESTIONS_DIR: qdir, ASD_MERGE_BIN: slow, ASD_QUESTIONS_NO_SLACK: "1", ASD_QUESTIONS_NO_NOTIFY: "1",
      ASD_DISK_WATCH: "0", ASD_STUB_FETCH_SECS: "0", ASD_LAG_WATCH: "0", ASD_MERGE_DETECTOR: "0", ASD_DM_AGENT: "0", ASD_OPEN_WORK_DIR: join(dir, "ow"), ASD_SLACK_USER: "" },
  })
  try {
    const base = `http://127.0.0.1:${port}`
    for (let i = 0; i < 100; i++) { try { await fetch(base + "/api/questions"); break } catch { await Bun.sleep(100) } }
    const t0 = Date.now()
    const merge = fetch(base + "/api/runs/repo/W-1/merge").then((r) => r.json())
    await Bun.sleep(300)   // the merge call is now in its 3 s sleep
    const t1 = Date.now()
    const ans = await fetch(base + "/api/questions/repo.W-1.Q1/messages", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ kind: "answer", option: "A" }) })
    expect(ans.status).toBe(201)
    expect(Date.now() - t1).toBeLessThan(1500)
    const list = await fetch(base + "/api/questions")
    expect(list.status).toBe(200)
    expect(Date.now() - t1).toBeLessThan(1500)
    const m: any = await merge
    expect(Date.now() - t0).toBeGreaterThan(2500)
    expect(m.text).toContain("would merge")
  } finally { proc.kill() }
}, 60_000)

test("a run whose live store fails (or comes back empty) keeps its scanned state instead of crashing getRuns", async () => {
  const { getRuns } = await freshRuns("livefail")
  const build = async (_q: any) => ({ ...seeded, scannedAt: "live" })
  const v = await getRuns(() => [], { fresh: true, build, live: () => Promise.reject(new Error("disk gone")) })
  expect(v.runs.map((r) => r.id)).toEqual(["W-1"])
  expect(v.runs[0].running).toBe(false)
  const w = await getRuns(() => [], { fresh: true, build, live: () => Promise.resolve(undefined as any) })
  expect(w.runs.length).toBe(1)
})
