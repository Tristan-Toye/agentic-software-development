// Dossier ids a LIVE /plan run has claimed under another run name. Contract: references/runner.md.
// A /plan started from a free-text seed runs as `<repo>.seed-<slug>` (a plan-from-deferred run as `<repo>.<ID>.plan-D-n`),
// claims the dossier id in Phase 1 and names it in every later `stage` event ("W-218 (claimed, final), key ..."). The
// dossier reads `planned` meanwhile, so no Start/Plan start may be offered for it. A faithful port of
// scripts/planning_runs.py; both are pinned against test/fixtures/planning-runs by their tests.
import { readdirSync, readFileSync } from "node:fs"
import { join } from "node:path"
import { readdir } from "node:fs/promises"
import { runsDir } from "./runstore.ts"
import { fileSig, readText } from "./asyncio.ts"

export type PlanningRun = { key: string; state: string }
const LIVE = ["running", "testing", "waiting", "queued", "paused", "parked"]
const CLAIMED = /\b([A-Z][A-Z0-9]*-\d+)\s*\(?claimed/g   // "W-218 (claimed, final)" and "W-219 claimed (claim_id exit 0)"
// A stage text that opens with the id ("Dossier W-035 (minted ...", "W-035, local mode ..."): the claim, when two stage
// events of the run open with the same id (one alone may name a source dossier).
const LEADING = /^\s*(?:Dossier\s+)?([A-Z][A-Z0-9]*-\d+)\b/
/** Ids of the questions `<repo>.<ID>.Qn` a run asked: once /plan claimed an id, its questions carry it. */
const questionIds = (repo: string, text: string) => [...text.matchAll(new RegExp(`\\b${repo.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\.([A-Z][A-Z0-9]*-\\d+)\\.Q\\d+\\b`, "g"))].map((m) => m[1])

/** The ids one run's log claims, in log order: "claimed" in stage events, an id two stage events open with, and the ids
 *  of the questions in runner lines. */
function logClaims(repo: string, text: string): string[] {
  const ids: string[] = []; const leads = new Map<string, number>()
  for (const line of text.split("\n")) {
    if (!line.includes("claimed") && !line.includes("Q") && !/"kind":\s*"stage"/.test(line)) continue
    let o: any
    try { o = JSON.parse(line) } catch { continue }
    if (o?.kind === "runner") for (const id of questionIds(repo, String(o.text ?? ""))) ids.push(id.toUpperCase())
    if (o?.kind !== "stage") continue
    for (const m of `${o.text ?? ""} ${o.stage ?? ""}`.matchAll(CLAIMED)) ids.push(m[1].toUpperCase())
    const lead = String(o.text ?? "").match(LEADING)
    if (lead) { const id = lead[1].toUpperCase(); leads.set(id, (leads.get(id) ?? 0) + 1); if (leads.get(id) === 2) ids.push(id) }
  }
  return ids
}

/** {dossier id -> the live planning run that claimed it} for one repo; the first run (by key) wins an id. */
export function planningRuns(repo: string, dir = runsDir()): Record<string, PlanningRun> {
  const out: Record<string, PlanningRun> = {}
  let names: string[] = []
  try { names = readdirSync(dir).filter((n) => n.startsWith(repo + ".") && n.endsWith(".runner.json")).sort() } catch { return out }
  for (const n of names) {
    const key = n.slice(0, -".runner.json".length)
    const short = key.slice(repo.length + 1)
    if (!(short.startsWith("seed-") || /\.plan-D-\d+$/.test(short))) continue
    let st: { command?: string; state?: string; questions?: string[] } = {}
    try { st = JSON.parse(readFileSync(join(dir, n), "utf8")) || {} } catch { continue }
    if (st.command !== "plan" || !st.state || !LIVE.includes(st.state)) continue
    for (const id of questionIds(repo, (st.questions ?? []).join(" "))) out[id.toUpperCase()] ??= { key, state: st.state }
    let text = ""
    try { text = readFileSync(join(dir, `${key}.jsonl`), "utf8") } catch { continue }
    for (const id of logClaims(repo, text)) out[id] ??= { key, state: st.state }
  }
  return out
}

/** The claims of one run's log, by file signature: an unchanged log is not read or parsed again. */
const claimCache = new Map<string, { sig: string; ids: string[] }>()

/** `planningRuns` without a single synchronous read: one async listing, async reads, a run's log re-read only when it changed. */
export async function planningRunsAsync(repo: string, dir = runsDir()): Promise<Record<string, PlanningRun>> {
  const out: Record<string, PlanningRun> = {}
  let names: string[] = []
  try { names = (await readdir(dir)).filter((n) => n.startsWith(repo + ".") && n.endsWith(".runner.json")).sort() } catch { return out }
  for (const n of names) {
    const key = n.slice(0, -".runner.json".length)
    const short = key.slice(repo.length + 1)
    if (!(short.startsWith("seed-") || /\.plan-D-\d+$/.test(short))) continue
    const rtext = await readText(join(dir, n))
    if (rtext === null) continue
    let st: { command?: string; state?: string; questions?: string[] } = {}
    try { st = JSON.parse(rtext) || {} } catch { continue }
    if (st.command !== "plan" || !st.state || !LIVE.includes(st.state)) continue
    for (const id of questionIds(repo, (st.questions ?? []).join(" "))) out[id.toUpperCase()] ??= { key, state: st.state }
    const path = join(dir, `${key}.jsonl`)
    const sig = await fileSig(path)
    if (sig === null) { claimCache.delete(path); continue }
    let hit = claimCache.get(path)
    if (!hit || hit.sig !== sig) {
      const text = await readText(path)
      if (text === null) continue
      hit = { sig, ids: logClaims(repo, text) }
      claimCache.set(path, hit)
    }
    for (const id of hit.ids) out[id] ??= { key, state: st.state }
  }
  return out
}
