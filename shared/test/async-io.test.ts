import { afterAll, beforeAll, describe, expect, test } from "bun:test"
import { appendFileSync, mkdtempSync, rmSync, utimesSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { mapLimit } from "../questions/asyncio.ts"
import { readAll, readAllAsync } from "../questions/store.ts"
import { readUpdates, readUpdatesAsync, readUpdatesSig } from "../questions/runstore.ts"

let dir: string
beforeAll(() => { dir = mkdtempSync(join(tmpdir(), "asd-async-io-")) })
afterAll(() => rmSync(dir, { recursive: true, force: true }))

const qline = (text: string, ts = "2026-01-01T00:00:00.000Z") => JSON.stringify({ ts, from: "agent", kind: "question", text, repo: "r", dossier: "D-1", root: "/x" }) + "\n"
const uline = (text: string, ts: string) => JSON.stringify({ ts, kind: "update", text }) + "\n"
/** Rewrite a file with other content of the same size and the same mtime: only a re-read can notice. */
const FIXED = new Date("2026-01-01T00:00:00.000Z")
function pin(path: string) { utimesSync(path, FIXED, FIXED) }
function swapQuietly(path: string, text: string) {
  writeFileSync(path, text)
  pin(path)
}

describe("mapLimit", () => {
  test("never runs more than the limit at once and keeps the order", async () => {
    let live = 0, peak = 0
    const out = await mapLimit([...Array(40).keys()], 16, async (i) => { live++; peak = Math.max(peak, live); await new Promise((r) => setTimeout(r, 2)); live--; return i * 2 })
    expect(peak).toBeLessThanOrEqual(16)
    expect(out).toEqual([...Array(40).keys()].map((i) => i * 2))
  })
})

describe("readAllAsync", () => {
  test("matches readAll, and an unchanged file is not read again", async () => {
    const qd = mkdtempSync(join(dir, "q-"))
    for (const id of ["a", "b", "c"]) writeFileSync(join(qd, `${id}.jsonl`), qline(`question ${id}`, `2026-01-0${id.charCodeAt(0) - 96}T00:00:00.000Z`))
    writeFileSync(join(qd, "a.slack.json"), "{}")
    for (const id of ["a", "b", "c"]) pin(join(qd, `${id}.jsonl`))
    expect(await readAllAsync(qd)).toEqual(readAll(qd))
    // same size and mtime, different content: the cache answers, so the file was not read
    swapQuietly(join(qd, "a.jsonl"), qline("question Z", "2026-01-01T00:00:00.000Z"))
    expect((await readAllAsync(qd)).find((q) => q.id === "a")!.text).toBe("question a")
  })
  test("an appended file is re-read and shows its new lines", async () => {
    const qd = mkdtempSync(join(dir, "q-"))
    writeFileSync(join(qd, "a.jsonl"), qline("q"))
    expect((await readAllAsync(qd))[0].lines.length).toBe(1)
    appendFileSync(join(qd, "a.jsonl"), JSON.stringify({ ts: "2026-01-01T00:01:00.000Z", from: "owner", kind: "answer", text: "yes", option: "y", via: "cli" }) + "\n")
    const q = (await readAllAsync(qd))[0]
    expect(q.lines.length).toBe(2)
    expect(q.state).toBe("answered")
  })
  test("a deleted file disappears", async () => {
    const qd = mkdtempSync(join(dir, "q-"))
    writeFileSync(join(qd, "a.jsonl"), qline("q"))
    expect((await readAllAsync(qd)).length).toBe(1)
    rmSync(join(qd, "a.jsonl"))
    expect((await readAllAsync(qd)).length).toBe(0)
  })
})

describe("readUpdatesSig / readUpdatesAsync", () => {
  test("same result as readUpdates", async () => {
    const rd = mkdtempSync(join(dir, "r-"))
    writeFileSync(join(rd, "repo.X-1.jsonl"), uline("one", "t1") + "not json\n" + uline("two", "t2"))
    expect(await readUpdatesAsync("repo.X-1", rd, 100000)).toEqual(readUpdates("repo.X-1", rd, 100000))
    expect(await readUpdatesAsync("repo.nothing", rd)).toEqual([])
  })
  test("an unchanged log is reported unchanged and not re-read", async () => {
    const rd = mkdtempSync(join(dir, "r-"))
    const f = join(rd, "repo.X-2.jsonl")
    writeFileSync(f, uline("one", "t1"))
    pin(f)
    expect((await readUpdatesSig("repo.X-2", rd)).changed).toBe(true)
    swapQuietly(f, uline("two", "t1"))
    const again = await readUpdatesSig("repo.X-2", rd)
    expect(again.changed).toBe(false)
    expect(again.all.map((u) => u.text)).toEqual(["one"])
  })
  test("an appended log yields only the new lines past those already seen", async () => {
    const rd = mkdtempSync(join(dir, "r-"))
    const f = join(rd, "repo.X-3.jsonl")
    writeFileSync(f, uline("one", "t1") + uline("two", "t2"))
    const first = await readUpdatesSig("repo.X-3", rd)
    const seen = first.all.length
    appendFileSync(f, uline("three", "t3") + uline("four", "t4"))
    const next = await readUpdatesSig("repo.X-3", rd)
    expect(next.changed).toBe(true)
    // chronological, as scanRunThreads consumes it: the lines after `seen` are exactly the appended ones, in order
    expect([...next.all].reverse().slice(seen).map((u) => u.text)).toEqual(["three", "four"])
  })
})
