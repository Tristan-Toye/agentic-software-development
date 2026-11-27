import { afterAll, beforeAll, describe, expect, test } from "bun:test"
import { appendFileSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { appendOwner, INDEX_FILE, noteQuestion, readAllAsync, readOpenAsync } from "../questions/store.ts"

let root: string
beforeAll(() => { root = mkdtempSync(join(tmpdir(), "asd-open-index-")) })
afterAll(() => rmSync(root, { recursive: true, force: true }))

const qline = (text: string, ts = "2026-01-01T00:00:00.000Z") => JSON.stringify({ ts, from: "agent", kind: "question", text, repo: "r", dossier: "D-1", root: "/x", options: [{ key: "A", label: "yes" }] }) + "\n"
const answer = JSON.stringify({ ts: "2026-01-02T00:00:00.000Z", from: "owner", kind: "answer", text: "A", option: "A", via: "cli" }) + "\n"
let n = 0
const fresh = () => { const d = join(root, `q${n++}`); require("node:fs").mkdirSync(d); return d }
const index = (d: string) => JSON.parse(readFileSync(join(d, INDEX_FILE), "utf8"))
const ids = (qs: { id: string }[]) => qs.map((q) => q.id).sort()

describe("open-question index", () => {
  test("a new question appears, and the index is written", async () => {
    const d = fresh()
    writeFileSync(join(d, "r.D-1.Q1.jsonl"), qline("one"))
    expect(ids(await readOpenAsync(d))).toEqual(["r.D-1.Q1"])
    expect(index(d)).toEqual({ v: 1, open: ["r.D-1.Q1"], closed: [] })
    writeFileSync(join(d, "r.D-1.Q2.jsonl"), qline("two", "2026-01-01T00:00:01.000Z"))
    expect(ids(await readOpenAsync(d))).toEqual(["r.D-1.Q1", "r.D-1.Q2"])
    expect(index(d).open.sort()).toEqual(["r.D-1.Q1", "r.D-1.Q2"])
  })

  test("answering removes it from the open set and moves it to closed; it stays in the full list", async () => {
    const d = fresh()
    writeFileSync(join(d, "r.D-1.Q1.jsonl"), qline("one"))
    writeFileSync(join(d, "r.D-1.Q2.jsonl"), qline("two", "2026-01-01T00:00:01.000Z"))
    await readOpenAsync(d)
    appendOwner(d, "r.D-1.Q1", { kind: "answer", text: "A", option: "A", via: "page" })
    expect(ids(await readOpenAsync(d))).toEqual(["r.D-1.Q2"])
    expect(index(d)).toEqual({ v: 1, open: ["r.D-1.Q2"], closed: ["r.D-1.Q1"] })
    const all = await readAllAsync(d)
    expect(all.map((q) => [q.id, q.state])).toEqual([["r.D-1.Q1", "answered"], ["r.D-1.Q2", "open"]])
  })

  test("a direct file write that bypasses the index is still found", async () => {
    const d = fresh()
    writeFileSync(join(d, "r.D-1.Q1.jsonl"), qline("one"))
    await readOpenAsync(d)
    writeFileSync(join(d, "r.D-1.Q2.jsonl"), qline("two", "2026-01-01T00:00:01.000Z"))
    expect(ids(await readOpenAsync(d))).toEqual(["r.D-1.Q1", "r.D-1.Q2"])
    // an answer appended by hand, and a reply on an open question, are seen without any note
    appendFileSync(join(d, "r.D-1.Q1.jsonl"), answer)
    appendFileSync(join(d, "r.D-1.Q2.jsonl"), JSON.stringify({ ts: "2026-01-03T00:00:00.000Z", from: "agent", kind: "reply", text: "hi" }) + "\n")
    const open = await readOpenAsync(d)
    expect(ids(open)).toEqual(["r.D-1.Q2"])
    expect(open[0].lines.length).toBe(2)
  })

  test("a missing or corrupt index is rebuilt from a full scan, and a bad entry never hides an open question", async () => {
    const d = fresh()
    writeFileSync(join(d, "r.D-1.Q1.jsonl"), qline("one"))
    writeFileSync(join(d, "r.D-1.Q2.jsonl"), qline("two", "2026-01-01T00:00:01.000Z") + answer)
    await readOpenAsync(d)
    rmSync(join(d, INDEX_FILE))
    // a new process view: the in-memory state is per dir, so use a fresh dir copy to force a cold start
    const cold = fresh()
    for (const f of ["r.D-1.Q1.jsonl", "r.D-1.Q2.jsonl"]) writeFileSync(join(cold, f), readFileSync(join(d, f)))
    expect(existsSync(join(cold, INDEX_FILE))).toBe(false)
    expect(ids(await readOpenAsync(cold))).toEqual(["r.D-1.Q1"])
    expect(index(cold)).toEqual({ v: 1, open: ["r.D-1.Q1"], closed: ["r.D-1.Q2"] })
    // corrupt index, plus a lying one that claims the open question is closed and lists a missing file
    const bad = fresh()
    for (const f of ["r.D-1.Q1.jsonl", "r.D-1.Q2.jsonl"]) writeFileSync(join(bad, f), readFileSync(join(d, f)))
    writeFileSync(join(bad, INDEX_FILE), "{not json")
    expect(ids(await readOpenAsync(bad))).toEqual(["r.D-1.Q1"])
    expect(index(bad).closed).toEqual(["r.D-1.Q2"])
    const liar = fresh()
    for (const f of ["r.D-1.Q1.jsonl", "r.D-1.Q2.jsonl"]) writeFileSync(join(liar, f), readFileSync(join(d, f)))
    writeFileSync(join(liar, INDEX_FILE), JSON.stringify({ v: 1, open: ["r.D-1.Q9"], closed: [] }))
    expect(ids(await readOpenAsync(liar))).toEqual(["r.D-1.Q1"])
    expect(index(liar)).toEqual({ v: 1, open: ["r.D-1.Q1"], closed: ["r.D-1.Q2"] })
  })

  test("two writer processes do not lose an id", async () => {
    const d = fresh()
    writeFileSync(join(d, "seed.jsonl"), qline("seed"))
    await readOpenAsync(d)
    const script = join(root, "writer.ts")
    writeFileSync(script, `import { noteQuestion } from ${JSON.stringify(join(import.meta.dir, "../questions/store.ts"))}\nconst [d, p] = process.argv.slice(2)\nfor (let i = 0; i < 40; i++) noteQuestion(d, p + i, "open")\n`)
    const run = (p: string) => Bun.spawn(["bun", script, d, p], { stdout: "ignore", stderr: "inherit" }).exited
    await Promise.all([run("a"), run("b"), run("c")])
    const open = new Set<string>(index(d).open)
    for (const p of ["a", "b", "c"]) for (let i = 0; i < 40; i++) expect(open.has(p + i)).toBe(true)
    expect(open.has("seed")).toBe(true)
    expect(existsSync(join(d, "open-index.lock"))).toBe(false)
  })

  test("the Python writer's note is understood (ask_owner.index_note)", async () => {
    const d = fresh()
    writeFileSync(join(d, "r.D-1.Q1.jsonl"), qline("one"))
    await readOpenAsync(d)
    const py = Bun.spawnSync(["python3", "-c", "import sys; sys.path.insert(0, sys.argv[1]); import ask_owner; ask_owner.index_note('r.D-1.Q1', 'closed'); ask_owner.index_note('r.D-1.Q7', 'open')", join(import.meta.dir, "../../scripts")], { env: { ...process.env, ASD_QUESTIONS_DIR: d } })
    expect(py.exitCode).toBe(0)
    expect(index(d)).toEqual({ v: 1, open: ["r.D-1.Q7"], closed: ["r.D-1.Q1"] })
  })

  test("noteQuestion leaves a missing index alone", () => {
    const d = fresh()
    noteQuestion(d, "x", "open")
    expect(existsSync(join(d, INDEX_FILE))).toBe(false)
  })
})
