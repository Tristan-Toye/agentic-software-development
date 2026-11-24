// The per-revision cache of the computed visual review (vrcache.ts, used by server.ts around visualReview / collapsedView).
// Run: bun test shared/test/vrcache.test.ts
import { afterAll, describe, expect, test } from "bun:test"
import { cpSync, mkdirSync, mkdtempSync, readFileSync, utimesSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { makeVrCache, vrSignature } from "../questions/vrcache.ts"
import { closeLayout, visualReview } from "../questions/vrlayout.ts"

const FIX = join(import.meta.dir, "..", "..", "references", "visual-review", "fixtures")
const dirWith = (): string => {
  const d = mkdtempSync(join(tmpdir(), "vrc-"))
  for (const v of ["l0", "l1", "l2"]) cpSync(join(FIX, `w145-${v}.json`), join(d, `${v}.json`))
  return d
}
afterAll(() => closeLayout())

describe("makeVrCache", () => {
  test("the same key and signature computes once (hit), a new signature computes again (miss)", async () => {
    const c = makeVrCache<number>(); let n = 0
    const f = async () => ++n
    expect(await c.get("k", "s1", f)).toBe(1)
    expect(await c.get("k", "s1", f)).toBe(1)
    expect(await c.get("k", "s2", f)).toBe(2)
    expect(await c.get("k", "s2", f)).toBe(2)
    expect(c.stats()).toEqual({ hits: 2, misses: 2, size: 1 })
  })
  test("a second request while the first runs waits for it instead of computing again", async () => {
    const c = makeVrCache<number>(); let n = 0
    const f = () => new Promise<number>((ok) => setTimeout(() => ok(++n), 20))
    const [a, b] = await Promise.all([c.get("k", "s", f), c.get("k", "s", f)])
    expect([a, b, n]).toEqual([1, 1, 1])
  })
  test("a rejection is not stored: the next request computes again", async () => {
    const c = makeVrCache<number>(); let n = 0
    await expect(c.get("k", "s", async () => { n++; throw new Error("boom") })).rejects.toThrow("boom")
    expect(await c.get("k", "s", async () => ++n)).toBe(2)
  })
  test("it holds at most `max` results, dropping the least recently used", async () => {
    const c = makeVrCache<string>(2)
    await c.get("a", "s", async () => "A"); await c.get("b", "s", async () => "B")
    await c.get("a", "s", async () => "A2") // touch a: b is now the oldest
    await c.get("c", "s", async () => "C")
    expect(c.stats().size).toBe(2)
    expect(await c.get("a", "s", async () => "A3")).toBe("A")
    expect(await c.get("b", "s", async () => "B2")).toBe("B2")
  })
})

describe("vrSignature and the real visualReview", () => {
  test("a cached review is the same object on a hit, and any change of a graph file is a miss", async () => {
    const d = dirWith(), c = makeVrCache<Awaited<ReturnType<typeof visualReview>>>(); let runs = 0
    const get = () => c.get(d, vrSignature(d), () => { runs++; return visualReview(d, "W-145") })
    const first = await get()
    expect(first.issues).toEqual([])
    expect(await get()).toBe(first)
    expect(runs).toBe(1)
    // same size, later mtime: still a miss
    const t = new Date(Date.now() + 5000)
    utimesSync(join(d, "l1.json"), t, t)
    const second = await get()
    expect(runs).toBe(2)
    expect(second.rev).toBe(first.rev) // the content is the same: only the cache key moved
    // a changed file: a miss and a new revision
    const g = JSON.parse(readFileSync(join(d, "l0.json"), "utf8")); g.nodes[0].label = g.nodes[0].label + " (edited)"
    writeFileSync(join(d, "l0.json"), JSON.stringify(g))
    const third = await get()
    expect(runs).toBe(3)
    expect(third.rev).not.toBe(first.rev)
  })
  test("a new review.json or a new arch draft changes the signature; the content of review.json does not", () => {
    const d = dirWith(), s0 = vrSignature(d)
    writeFileSync(join(d, "review.json"), "{}"); const s1 = vrSignature(d)
    expect(s1).not.toBe(s0)
    writeFileSync(join(d, "review.json"), '{"lines":[]}'); expect(vrSignature(d)).toBe(s1)
    mkdirSync(join(d, "arch")); writeFileSync(join(d, "arch", "x.json"), "{}")
    expect(vrSignature(d)).not.toBe(s1)
  })
  test("server.ts puts both routes behind the cache and leaves vrlayout.ts alone", () => {
    const server = readFileSync(join(import.meta.dir, "..", "questions", "server.ts"), "utf8")
    expect(server).toContain("vrReviewCache.get(")
    expect(server).toContain("vrCollapsedCache.get(")
    expect(server).toContain("vrSignature(")
  })
})

describe("GET /api/visual-review through the server", () => {
  const root = mkdtempSync(join(tmpdir(), "vrc-route-")), repo = join(root, "repoC")
  mkdirSync(join(repo, ".discovery", "dossiers"), { recursive: true })
  writeFileSync(join(repo, ".discovery", "dossiers", "W-145.md"), "---\nid: W-145\ntitle: T\nstatus: ready\n---\nbody\n")
  const vdir = join(repo, ".discovery", "visual-review", "W-145")
  mkdirSync(vdir, { recursive: true })
  for (const v of ["l0", "l1", "l2"]) cpSync(join(FIX, `w145-${v}.json`), join(vdir, `${v}.json`))
  const cdir = mkdtempSync(join(tmpdir(), "vrc-route-x-"))
  let proc: ReturnType<typeof Bun.spawn> | undefined, base = ""
  afterAll(() => { proc?.kill() })
  async function start(): Promise<void> {
    if (base) return
    proc = Bun.spawn([process.execPath, "--no-install", join(import.meta.dir, "..", "questions", "server.ts")], {
      env: { ...process.env, ASD_QUESTIONS_DIR: join(cdir, "q"), ASD_QUESTIONS_PORT: "0", ASD_QUESTIONS_NO_NOTIFY: "1", ASD_DISK_WATCH: "0", ASD_LAG_WATCH: "0", ASD_QUESTIONS_NO_SLACK: "1", ASD_SLACK_USER: "", ASD_RUNS_ROOTS: repo, ASD_OPEN_WORK_DIR: join(cdir, "ow"), ASD_RUNS_DIR: join(cdir, "runs"), ASD_CLAUDE_PROJECTS: join(cdir, "projects"), ASD_RUNS_NO_GH: "1", ASD_BLOCK_LOG: join(cdir, "blocks.jsonl"), ASD_CHAT_DIR: join(cdir, "chats"), ASD_DM_AGENT_FILE: join(cdir, "dm.json") },
      stdout: "pipe", stderr: "ignore",
    })
    const reader = (proc.stdout as ReadableStream).getReader()
    let out = ""
    while (!/listening http:\/\/127\.0\.0\.1:(\d+)/.test(out)) { const { value, done } = await reader.read(); if (done) break; out += new TextDecoder().decode(value) }
    base = out.match(/listening (http:\/\/127\.0\.0\.1:\d+)/)?.[1] ?? ""
  }
  test("a repeat is served from the cache with the same revision; an edited graph file gives a new one at once", async () => {
    await start()
    const get = async () => (await (await fetch(`${base}/api/visual-review?dossier=W-145&repo=repoC`)).json()) as any
    const a = await get(), b = await get()
    expect(a.views.L0.layout.boxes).toBeDefined()
    expect(b.rev).toBe(a.rev)
    expect(b.views.L1.layout).toEqual(a.views.L1.layout)
    const g = JSON.parse(readFileSync(join(vdir, "l2.json"), "utf8")); g.nodes[0].label = g.nodes[0].label + " (edited)"
    writeFileSync(join(vdir, "l2.json"), JSON.stringify(g))
    const c = await get()
    expect(c.rev).not.toBe(a.rev)
    expect(c.views.L2.graph.nodes[0].label).toMatch(/\(edited\)$/)
  })
})
