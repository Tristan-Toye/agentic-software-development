// The review packet panes (packet.ts): the pure models, the DOM body, the cache signature and GET /api/review-packet.
// Run: bun test shared/test/packet.test.ts
import { afterAll, describe, expect, test } from "bun:test"
import { mkdirSync, mkdtempSync, utimesSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { hasPacket, packetBody, packetModel, packetSignature, readPacket } from "../questions/packet.ts"
import { PAGE } from "../questions/page.ts"

const CD = { producer: "contract-diff", dossier: "W-1", summary: { ERR: 1, WARN: 2, INFO: 40 },
  entries: [{ id: "a", name: "f", status: "changed", before: "f(a)", after: "f(a,b)", severity: "ERR", tier: "confirmed", rule: "arity", reason: "new required param", anchor: "src/a.rs:10" },
    { id: "b", severity: "WARN", tier: "cannot-confirm", rule: "r", reason: "x", anchor: null }], cannot_confirm: [], not_caught: ["macros"] }
const INV = { producer: "invariants", dossier: "W-1", rule: "anchored or speculative", items: [
  { id: "i1", kind: "invariant", text: "sorted", anchor: "src/a.rs:3", speculative: false, source: "contract-prose" },
  { id: "i2", kind: "failure_mode", text: "might time out", anchor: null, speculative: true, source: "authored" }] }

// A tiny element builder with the shape of the page's h(), to read the DOM back as text.
const h = (tag: string, a: any, kids: any[] = []): any => ({ tag, a: a || {}, kids: kids.flat(9).filter((k) => k !== null && k !== undefined && k !== false) })
const text = (n: any): string => (typeof n === "string" ? n : n.kids.map(text).join(" "))
const find = (n: any, tag: string, out: any[] = []): any[] => { if (typeof n === "string") return out; if (n.tag === tag) out.push(n); n.kids.forEach((k: any) => find(k, tag, out)); return out }

describe("packetModel", () => {
  test("contract diff: the badge counts entries and ERR, each row carries its anchor", () => {
    const m = packetModel("contract-diff", CD)
    expect(m.title).toBe("Contract diff")
    expect(m.badge.text).toBe("2 · 1 ERR · 2 WARN")
    expect(m.badge.tone).toBe("warn")
    expect(m.sections[0].rows[0].at(-1)).toEqual({ t: "src/a.rs:10", a: "src/a.rs:10" })
    expect(m.sections.map((s: any) => s.head)).toContain("Not caught by this check")
  })
  test("invariants: an item with no anchor is speculative even if the file does not say so", () => {
    const d = structuredClone(INV); d.items[1].speculative = false
    const m = packetModel("invariants", d)
    expect(m.badge.text).toBe("2 · 1 speculative")
    expect(m.sections[0].rows[1][2]).toEqual({ t: "speculative", spec: true })
    expect(m.sections[0].rows[0][2].a).toBe("src/a.rs:3")
  })
  test("criteria, reading order and coupling: counts in the badge", () => {
    expect(packetModel("criteria-evidence", { summary: { covered: 3, partial: 1, "no-evidence": 1, criteria: 5 }, rows: [{ criterion: 1, status: "covered", text: "t", tests: [{ file: "t/a.rs", line: 4, name: "n", result: "pass", found_by: "test-name" }], status_reasons: [], links: [], anchor: null }] }).badge.text).toBe("3/5 covered · 1 partial · 1 no evidence")
    expect(packetModel("reading-order", { listed_files: 4, total_changed_files: 9, order: [{ rank: 1, group: "contract", reason: "r", files: [{ path: "src/a.rs", added: 1, deleted: 2, signals: 0, callers: 3, why: "w" }] }], not_listed: { count: 5, over_cap: 3, noise: 2, noise_rule: "locks" } }).badge.text).toBe("4 of 9 files · 5 not listed")
    expect(packetModel("coupling-delta", { added: [{ from: "a", to: "b", anchor: null }], removed: [], new_cycles: [{ nodes: ["a", "b"] }], layer_breaks: [] }).badge).toEqual({ text: "+1 · 1 cycle", tone: "warn" })
  })
  test("a skipped producer is one note, no table; an unknown name is null", () => {
    const m = packetModel("reading-order", { skipped: "no base" })
    expect(m.badge.text).toBe("skipped"); expect(m.sections).toEqual([]); expect(m.notes[0]).toContain("no base")
    expect(packetModel("nope", {})).toBeNull()
    expect(packetModel("invariants", null)).toBeNull()
  })
  test("garbage fields do not throw", () => { for (const n of ["contract-diff", "criteria-evidence", "reading-order", "invariants", "coupling-delta"]) expect(() => packetModel(n, { entries: 5, rows: "x", order: [null], items: [1], added: [null], summary: 3 })).not.toThrow() })
})

describe("packetBody", () => {
  test("path:line links open the editor at the line; a test id is plain code; speculative shows as a chip", () => {
    const body = packetBody(h, packetModel("invariants", INV), "/repo/")
    const links = body.flatMap((n: any) => find(n, "a"))
    expect(links[0].a.href).toBe("vscode://file/repo/src/a.rs:3".replace("file/", "file//"))
    expect(text({ tag: "div", a: {}, kids: body })).toContain("speculative")
    const t = packetBody(h, packetModel("contract-diff", { ...CD, entries: [{ ...CD.entries[0], anchor: "tests::foo" }] }), "/repo")
    expect(t.flatMap((n: any) => find(n, "a")).length).toBe(0)
    expect(t.flatMap((n: any) => find(n, "code")).length).toBe(1)
  })
})

describe("readPacket and the page", () => {
  const root = mkdtempSync(join(tmpdir(), "pk-"))
  const dir = join(root, ".discovery", "review-packet", "W-1")
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, "contract-diff.json"), JSON.stringify(CD))
  writeFileSync(join(dir, "invariants.json"), "{not json")
  test("a missing file is absent, a broken one is listed, signature moves with the mtime", () => {
    expect(hasPacket(root, "W-1")).toBe(true); expect(hasPacket(root, "W-2")).toBe(false)
    const p = readPacket(root, "W-1")
    expect(Object.keys(p.files)).toEqual(["contract-diff"]); expect(p.unreadable).toEqual(["invariants"])
    const a = packetSignature(dir); const t = new Date(Date.now() + 5000); utimesSync(join(dir, "contract-diff.json"), t, t)
    expect(packetSignature(dir)).not.toBe(a)
  })
  test("the page script ships the packet functions and registers the panes", () => {
    expect(PAGE).toContain("function packetModel"); expect(PAGE).toContain("function packetBody"); expect(PAGE).toContain("/api/review-packet")
  })
})

describe("GET /api/review-packet", () => {
  const root = mkdtempSync(join(tmpdir(), "pk-route-"))
  const repo = join(root, "repoP")
  mkdirSync(join(repo, ".discovery", "dossiers"), { recursive: true })
  for (const id of ["W-145", "W-146"]) writeFileSync(join(repo, ".discovery", "dossiers", id + ".md"), `---\nid: ${id}\ntitle: T ${id}\nstatus: ready\n---\nbody\n`)
  const pdir = join(repo, ".discovery", "review-packet", "W-145")
  mkdirSync(pdir, { recursive: true })
  writeFileSync(join(pdir, "contract-diff.json"), JSON.stringify(CD))
  const cdir = mkdtempSync(join(tmpdir(), "pk-route-x-"))
  let proc: ReturnType<typeof Bun.spawn> | undefined
  let base = ""
  async function start(): Promise<void> {
    if (base) return
    proc = Bun.spawn([process.execPath, "--no-install", join(import.meta.dir, "..", "questions", "server.ts")], {
      env: { ...process.env, ASD_QUESTIONS_DIR: join(cdir, "q"), ASD_QUESTIONS_PORT: "0", ASD_QUESTIONS_NO_NOTIFY: "1", ASD_DISK_WATCH: "0", ASD_LAG_WATCH: "0", ASD_QUESTIONS_NO_SLACK: "1", ASD_SLACK_USER: "", ASD_RUNS_ROOTS: repo, ASD_ACTIVE_REPOS_FILE: join(cdir, "none.json"), ASD_OPEN_WORK_DIR: join(cdir, "ow"), ASD_RUNS_DIR: join(cdir, "runs"), ASD_CLAUDE_PROJECTS: join(cdir, "projects"), ASD_RUNS_NO_GH: "1", ASD_BLOCK_LOG: join(cdir, "blocks.jsonl"), ASD_CHAT_DIR: join(cdir, "chats"), ASD_DM_AGENT_FILE: join(cdir, "dm.json") },
      stdout: "pipe", stderr: "ignore",
    })
    const reader = (proc.stdout as ReadableStream).getReader()
    let out = ""
    while (!/listening http:\/\/127\.0\.0\.1:(\d+)/.test(out)) { const { value, done } = await reader.read(); if (done) break; out += new TextDecoder().decode(value) }
    base = out.match(/listening (http:\/\/127\.0\.0\.1:\d+)/)?.[1] ?? ""
  }
  afterAll(() => { proc?.kill() })

  test("200: the files that exist, parsed; the root for the editor links", async () => {
    await start()
    const r = await fetch(`${base}/api/review-packet?dossier=W-145`)
    expect(r.status).toBe(200)
    const j: any = await r.json()
    expect(Object.keys(j.files)).toEqual(["contract-diff"]); expect(j.files["contract-diff"].summary.ERR).toBe(1); expect(j.root).toBe(repo)
  })
  test("a change on disk is served after the file changes (cache keyed by size and mtime)", async () => {
    await start()
    writeFileSync(join(pdir, "reading-order.json"), JSON.stringify({ producer: "reading-order", dossier: "W-145", order: [], not_listed: { count: 0 } }))
    const j: any = await (await fetch(`${base}/api/review-packet?dossier=W-145`)).json()
    expect(Object.keys(j.files)).toEqual(["contract-diff", "reading-order"])
  })
  test("404 without a packet or an unknown dossier, 400 for a name that is not plain (no path from the id)", async () => {
    await start()
    expect((await fetch(`${base}/api/review-packet?dossier=W-146`)).status).toBe(404)
    expect((await fetch(`${base}/api/review-packet?dossier=W-999`)).status).toBe(404)
    for (const bad of ["..%2F..%2Fetc", ".hidden", "a%2Fb", ""]) expect((await fetch(`${base}/api/review-packet?dossier=${bad}`)).status).toBe(400)
  })
})
