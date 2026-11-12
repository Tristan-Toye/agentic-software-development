// A run button that was pressed: its pending marker, kept by the server (dossier + action) so a re-render, another
// tab or the next poll never brings the button back while the run is starting. Contract: references/owner-questions.md (Dashboard).
//
// The window this closes: the runner script returns "started" before the runner writes `<key>.runner.json`, so for a
// moment the dossier reads as not running and its Start/Resume/Fix button would reappear. The marker bridges it:
//   hidden   -> the press was recorded and no run has shown up yet (within PENDING_TTL_MS)
//   running  -> the run is queued/running/waiting/paused: the normal controls hide the button; the marker is dropped
//   settled  -> the runner wrote a terminal state after the press (failed/stopped/done): the button returns by the normal rules
//   failed   -> the action itself failed (the spawn returned an error): the button returns, with the error as a note
//   expired  -> no runner.json appeared within PENDING_TTL_MS: the button returns, with a note
import { statSync } from "node:fs"
import { join } from "node:path"
import { runsDir } from "./runstore.ts"

export const PENDING_TTL_MS = 60_000
export type PendingState = "hidden" | "running" | "settled" | "failed" | "expired"
export type Mark = { at: number; err?: string }
/** What the dossier's run looks like now: live (queued/running/waiting/paused), or the mtime (ms) of a terminal runner.json. */
export type Seen = { active: boolean; settledAt?: number }

const marks = new Map<string, Mark>()
const notes = new Map<string, string>()

export const pendingKey = (repo: string, id: string, action: string): string => `${repo}/${id}:${action}`

/** Record a press, before the runner is spawned. */
export function markPending(repo: string, id: string, action: string, now = Date.now()): void {
  const k = pendingKey(repo, id, action)
  marks.set(k, { at: now }); notes.delete(k)
}
/** The action failed (or the process died without a queue entry): the button returns; `err` is shown beside it. */
export function failPending(repo: string, id: string, action: string, err: string): void {
  const k = pendingKey(repo, id, action)
  marks.delete(k); notes.set(k, err)
}
export function clearPending(repo: string, id: string, action: string): void {
  const k = pendingKey(repo, id, action); marks.delete(k); notes.delete(k)
}
export const resetPending = (): void => { marks.clear(); notes.clear() }

/** Pure: the state of one marker. A run that is live always wins; then a terminal write after the press; then the timeout. */
export function judgePending(m: Mark, seen: Seen, now: number, ttl = PENDING_TTL_MS): PendingState {
  if (m.err) return "failed"
  if (seen.active) return "running"
  if (seen.settledAt !== undefined && seen.settledAt >= m.at) return "settled"
  return now - m.at >= ttl ? "expired" : "hidden"
}

/** mtime (ms) of `<key>.runner.json`, or undefined. */
export function runnerWrittenAt(key: string, dir = runsDir()): number | undefined {
  try { return statSync(join(dir, `${key}.runner.json`)).mtimeMs } catch { return undefined }
}

type Ctl = { show: boolean; ok: boolean; reason: string; command?: string }
type RunLike = {
  repo: string; id: string; runKey?: string
  runner: { state: string } | null
  controls: Record<string, Ctl>
  deferred?: { items: { id: string; planning?: string }[] }
}
const LIVE = ["running", "waiting", "paused", "queued"]
const CONTROL_ACTIONS = ["start", "resume", "fix-deferred"]

/**
 * Apply the markers of one dossier: a pending action's control is hidden; `pending` lists the hidden actions
 * (`start`, `resume`, `fix-deferred`, `plan:<item>`) and `pendingNotes` the error or timeout note of an action that
 * came back. Stale markers (the run is live, or settled, or timed out) are dropped here, so the table cannot grow.
 */
export function applyPending<T extends RunLike>(run: T, now = Date.now(), written: (key: string) => number | undefined = (k) => runnerWrittenAt(k)): T & { pending: string[]; pendingNotes: Record<string, string> } {
  const prefix = `${run.repo}/${run.id}:`
  const pending: string[] = []
  const pendingNotes: Record<string, string> = {}
  const controls = { ...run.controls }
  const runnerState = run.runner?.state
  const key = run.runKey || `${run.repo}.${run.id}`
  for (const [k, m] of [...marks]) {
    if (!k.startsWith(prefix)) continue
    const action = k.slice(prefix.length)
    let seen: Seen
    if (action.startsWith("plan:")) {
      const it = run.deferred?.items.find((i) => i.id === action.slice(5))
      const st = it?.planning
      seen = { active: !!st && LIVE.includes(st), settledAt: st && !LIVE.includes(st) ? written(`${run.repo}.${run.id}.plan-${action.slice(5)}`) : undefined }
    } else {
      seen = { active: !!runnerState && LIVE.includes(runnerState), settledAt: runnerState && !LIVE.includes(runnerState) ? written(key) : undefined }
    }
    const s = judgePending(m, seen, now)
    if (s === "hidden") {
      pending.push(action)
      if (CONTROL_ACTIONS.includes(action) && controls[action]) controls[action] = { ...controls[action], show: false }
    } else {
      marks.delete(k)
      if (s === "expired") notes.set(k, `${action.replace(/^plan:/, "plan ")} did not start within ${Math.round(PENDING_TTL_MS / 1000)} s — try again`)
      else if (s === "failed") notes.set(k, m.err!)
      else notes.delete(k)
    }
  }
  for (const [k, n] of notes) if (k.startsWith(prefix)) pendingNotes[k.slice(prefix.length)] = n
  return { ...run, controls, pending, pendingNotes } as T & { pending: string[]; pendingNotes: Record<string, string> }
}

/** The stub's /stub run (key `<repo>.<slug>`): is its Resume button hidden because it was pressed and the run is not there yet, and the failure note. */
export function stubResumePending(repo: string, slug: string, runner: { state: string } | null, now = Date.now()): { hidden: boolean; note: string } {
  const run = { repo, id: slug, runner, controls: {} as Record<string, Ctl> }
  const r = applyPending(run, now)
  return { hidden: r.pending.includes("resume"), note: r.pendingNotes.resume ?? "" }
}
