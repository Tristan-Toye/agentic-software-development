// Automatic work, per repository (scripts/auto_work.py decides; this file only reads, builds the facts it is given, and starts it).
// Contract: references/owner-questions.md (Dashboard, "Automatic work").
import { existsSync, readFileSync, renameSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import { runsDir } from "./runstore.ts"
import { flowsFile, flowsOf, flowsOfAsync } from "./deliveryflows.ts"
import { readJsonAsync } from "./asyncio.ts"

export type AutoMember = { id: string; stage: string; state: string; error: string; rounds: number }
export type AutoPaused = { reason: string; kind: string; member: string; action: string; at: string }
export type AutoState = {
  repo: string; cycle: number; step: string; phase: string; started: string; stub: string; integrating: string
  paused: AutoPaused | null; members: AutoMember[]; note: string; request: string | null; ts: string
}
export type AutoView = { repo: string; root: string; on: boolean; state: AutoState | null }

export const autoWorkScript = (): string => process.env.ASD_AUTO_WORK_BIN || join(import.meta.dir, "..", "..", "scripts", "auto_work.py")
export const switchFile = (): string => process.env.ASD_STUB_AUTOMERGE_FILE || join(homedir(), ".agentic", "stub-auto-merge.json")

function readJson(path: string): any {
  try { return JSON.parse(readFileSync(path, "utf8")) } catch { return null }
}

/** The one switch of a repository: automatic work (and, with it, the scripted stub merge). Off unless the file says so. */
export const autoWorkOn = (repo: string, file = switchFile()): boolean => !!readJson(file)?.[repo]?.auto_work

export const autoWorkOnAsync = async (repo: string, file = switchFile()): Promise<boolean> => !!(await readJsonAsync(file))?.[repo]?.auto_work
export const eagerMergeOnAsync = async (repo: string, file = switchFile()): Promise<boolean> => (await readJsonAsync(file))?.[repo]?.eager_merge !== false
/** `factsOn` with the switch files read off the main thread. */
export const factsOnAsync = async (repo: string, file = switchFile(), flows = flowsFile()): Promise<boolean> =>
  (await autoWorkOnAsync(repo, file)) || (await eagerMergeOnAsync(repo, file)) || (await flowsOfAsync(repo, flows)).kanban

/** The eager-merge switch of a repository (each finished dossier joins the merge queue for the open stub), on unless set to false. It works with automatic work off. */
export const eagerMergeOn = (repo: string, file = switchFile()): boolean => readJson(file)?.[repo]?.eager_merge !== false
/** Whether a repository's facts go to the script: automatic work or eager merge is on. */
// The kanban pull (auto_work.py) runs on the kanban flow switch alone, so a kanban-on repository sends its facts too.
export const factsOn = (repo: string, file = switchFile(), flows = flowsFile()): boolean => autoWorkOn(repo, file) || eagerMergeOn(repo, file) || flowsOf(repo, flows).kanban
export function readAutoState(repo: string, dir = runsDir()): AutoState | null {
  return autoStateOf(repo, readJson(join(dir, `${repo}.auto-work.json`)))
}
export async function readAutoStateAsync(repo: string, dir = runsDir()): Promise<AutoState | null> {
  return autoStateOf(repo, await readJsonAsync(join(dir, `${repo}.auto-work.json`)))
}
function autoStateOf(repo: string, s: any): AutoState | null {
  if (!s || typeof s !== "object" || !Array.isArray(s.members)) return null
  return {
    repo, cycle: Number(s.cycle) || 0, step: String(s.step ?? "idle"), phase: String(s.phase ?? "idle"), started: String(s.started ?? ""), stub: String(s.stub ?? ""), integrating: String(s.integrate?.branch ?? ""),
    paused: s.paused ? { reason: String(s.paused.reason ?? ""), kind: String(s.paused.kind ?? ""), member: String(s.paused.member ?? ""), action: String(s.paused.action ?? ""), at: String(s.paused.at ?? "") } : null,
    members: s.members.map((m: any) => ({ id: String(m.id), stage: String(m.stage ?? ""), state: String(m.state ?? ""), error: String(m.error ?? ""), rounds: Number(m.rounds) || 0 })),
    note: String(s.note ?? ""), request: s.request ? String(s.request) : null, ts: String(s.ts ?? ""),
  }
}

type RunLike = { repo: string; root: string; main?: string; id: string; status: string; blockedBy: string[]; runner: { state: string; command?: string; pid: number; started: string } | null; deferred: { items: { id: string; state: string }[] }; followUps?: { parent: string; key: string; kind: string; summary: string; seed: string; planKey: string; src?: string; item?: string; dossier: string; legacy: boolean; updated: string }[] }
type StubLike = { branch: string; state: string; canIntegrate?: boolean }

/** The facts `auto_work.py --tick` reads: per repository its root, its dossiers (status, blockers, runner, deferred items) and its open stubs. */
export function buildFacts(runs: RunLike[], stubs: { repo: string; main: string; stubs: StubLike[] }[], on: (repo: string) => boolean = factsOn): { repos: Record<string, any> } {
  const repos: Record<string, any> = {}
  for (const r of runs) {
    const root = r.main || r.root   // the repository's main checkout, as Start and Fix all deferred use
    if (!on(r.repo) || !root) continue
    const f = (repos[r.repo] ??= { root, runs: [], stubs: [], mergedStubs: [], followUps: [] as any[] })
    f.runs.push({
      id: r.id, status: r.status, blockedBy: r.blockedBy,
      runner: r.runner ? { state: r.runner.state, command: r.runner.command ?? "", pid: r.runner.pid, started: r.runner.started } : null,
      deferred: (r.deferred?.items ?? []).map((i) => ({ id: i.id, state: i.state })),
    })
    // The owner-ruled follow-up dossiers with no dossier (markers, deferred items routed to a dossier, legacy forms): the script starts their /plan.
    for (const u of r.followUps ?? []) f.followUps.push({ parent: u.parent, key: u.key, kind: u.kind, summary: u.summary, seed: u.seed, planKey: u.planKey, src: u.src ?? "", item: u.item ?? "", dossier: u.dossier, legacy: u.legacy, updated: u.updated })
  }
  for (const s of stubs) {
    const f = repos[s.repo]
    if (f) f.mergedStubs = s.stubs.filter((x) => x.state === "merged").map((x) => x.branch)   // GitHub says the PR merged, by land_stub.py or by hand
    if (f) f.stubs = s.stubs.filter((x) => x.state !== "merged").map((x) => ({ branch: x.branch, state: x.state, canIntegrate: !!x.canIntegrate }))
  }
  return { repos }
}

/** What the page shows per repository: the switch and the cycle. Only repositories with a dossier are listed. */
export function viewsFor(runs: { repo: string; root: string; main?: string }[], on: (repo: string) => boolean = autoWorkOn, state: (repo: string) => AutoState | null = readAutoState): AutoView[] {
  const seen = new Map<string, string>()
  for (const r of runs) if ((r.main || r.root) && !seen.has(r.repo)) seen.set(r.repo, r.main || r.root)
  return [...seen].map(([repo, root]) => ({ repo, root, on: on(repo), state: state(repo) }))
}

/** `viewsFor` with the switch and the state files read off the main thread: the request path's version. */
export async function viewsForAsync(runs: { repo: string; root: string; main?: string }[], file = switchFile(), dir = runsDir()): Promise<AutoView[]> {
  const on = new Map<string, boolean>(), st = new Map<string, AutoState | null>()
  const repos = [...new Set(runs.map((r) => r.repo))]
  await Promise.all(repos.map(async (r) => { on.set(r, await autoWorkOnAsync(r, file)); st.set(r, await readAutoStateAsync(r, dir)) }))
  return viewsFor(runs, (r) => on.get(r) ?? false, (r) => st.get(r) ?? null)
}

export const AUTO_ACTIONS = ["enable", "disable", "start-now", "resume"] as const
export type AutoAction = (typeof AUTO_ACTIONS)[number]

/** argv of the script for a button; null when the action is not one. */
export const argvFor = (action: string, root: string): string[] | null =>
  (AUTO_ACTIONS as readonly string[]).includes(action) ? ["python3", autoWorkScript(), `--${action}`, "--root", root] : null

export async function runAutoWork(argv: string[]): Promise<{ ok: boolean; output: string }> {
  const p = Bun.spawn(argv, { stdout: "pipe", stderr: "pipe", stdin: "ignore" })
  const [out, err] = await Promise.all([new Response(p.stdout).text(), new Response(p.stderr).text()])
  const code = await p.exited
  return { ok: code === 0, output: (out + err).trim().slice(0, 400) }
}

/** One look: the dashboard's 2-minute interval hands the script the facts on stdin. Fire and forget. */
export function tickAutoWork(facts: { repos: Record<string, any> }): void {
  if (!Object.keys(facts.repos).length) return
  const p = Bun.spawn(["python3", autoWorkScript(), "--tick", "--facts", "-"], { stdout: "ignore", stderr: "ignore", stdin: "pipe" })
  p.stdin.write(JSON.stringify(facts))
  p.stdin.end()
  p.unref()
}

/**
 * A stub whose PR GitHub shows merged gets `merged` in its integrate file, so every reader agrees (land_stub.py writes it for its own
 * merges; a merge by hand left it at `stub`). Only a file that exists and is not merged yet is touched. Returns whether it wrote.
 */
export function markMerged(key: string, dir = runsDir(), now = new Date()): boolean {
  const path = join(dir, `${key}.integrate.json`)
  if (!existsSync(path)) return false
  const cur = readJson(path)
  if (!cur || typeof cur !== "object" || cur.state === "merged") return false
  const tmp = `${path}.${process.pid}.tmp`
  writeFileSync(tmp, JSON.stringify({ ...cur, state: "merged", note: "merged into development", ts: now.toISOString().replace(/\.\d+Z$/, "Z") }, null, 1))
  renameSync(tmp, path)
  return true
}
