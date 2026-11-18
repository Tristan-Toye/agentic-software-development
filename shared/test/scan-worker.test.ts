import { afterAll, beforeAll, expect, test } from "bun:test"
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"

// The server runs the 30 s rescans on a worker thread (scanpool.ts); it must build exactly the view the in-thread scan does.
const KEYS = ["ASD_RUNS_ROOTS", "ASD_CLAUDE_PROJECTS", "ASD_RUNS_DIR", "ASD_RUNS_NO_GH", "ASD_VIEW_CACHE_DIR", "ASD_STUBS_NO_GIT"]
const saved: Record<string, string | undefined> = {}
let dir: string
beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), "asd-scan-worker-"))
  const root = join(dir, "repo")
  mkdirSync(join(root, ".discovery", "dossiers"), { recursive: true })
  writeFileSync(join(root, ".discovery", "dossiers", "W-1.md"), "---\nid: W-1\ntitle: One\nstatus: ready\n---\n\n## Problem\nx\n")
  writeFileSync(join(root, ".discovery", "dossiers", "W-2.md"), "---\nid: W-2\ntitle: Two\nstatus: building\n---\n\n## Build log\n- Phase 4: fan-out\n")
  mkdirSync(join(dir, "projects")); mkdirSync(join(dir, "runs")); mkdirSync(join(dir, "vc"))
  writeFileSync(join(dir, "runs", "repo.W-2.jsonl"), JSON.stringify({ ts: "2026-01-01T00:00:00Z", kind: "update", text: "hello" }) + "\n")
  for (const k of KEYS) saved[k] = process.env[k]
  Object.assign(process.env, { ASD_RUNS_ROOTS: root, ASD_CLAUDE_PROJECTS: join(dir, "projects"), ASD_RUNS_DIR: join(dir, "runs"), ASD_RUNS_NO_GH: "1", ASD_VIEW_CACHE_DIR: join(dir, "vc"), ASD_STUBS_NO_GIT: "1" })
})
afterAll(() => {
  for (const k of KEYS) { if (saved[k] === undefined) delete process.env[k]; else process.env[k] = saved[k] }
  rmSync(dir, { recursive: true, force: true })
})

const strip = (v: any) => JSON.parse(JSON.stringify(v, (k, x) => (k === "scannedAt" ? undefined : x)))

test("the worker builds the same runs and stubs views as the in-thread scan", async () => {
  const { buildRuns } = await import("../questions/runs.ts")
  const { buildStubsWithRoots } = await import("../questions/stubs.ts")
  const { enableScanWorker, offThread } = await import("../questions/scanpool.ts")
  const inRuns = await buildRuns([])
  const inStubs = await buildStubsWithRoots([])
  expect(inRuns.runs.map((r) => r.id).sort()).toEqual(["W-1", "W-2"])
  enableScanWorker()
  let called = false
  const wRuns = await offThread("runs", [], async () => { called = true; return buildRuns([]) })
  const wStubs = await offThread("stubs", [], async () => { called = true; return buildStubsWithRoots([]) })
  expect(called).toBe(false)
  expect(strip(wRuns)).toEqual(strip(inRuns))
  expect(strip(wStubs)).toEqual(strip(inStubs))
}, 30_000)
