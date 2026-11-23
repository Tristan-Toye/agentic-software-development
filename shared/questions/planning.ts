// Dossier ids a LIVE /plan run has claimed under another run name. Contract: references/runner.md.
// A /plan started from a free-text seed runs as `<repo>.seed-<slug>` (a plan-from-deferred run as `<repo>.<ID>.plan-D-n`),
// claims the dossier id in Phase 1 and names it in every later `stage` event ("W-218 (claimed, final), key ..."). The
// dossier reads `planned` meanwhile, so no Start/Plan start may be offered for it. A faithful port of
// scripts/planning_runs.py; both are pinned against test/fixtures/planning-runs by their tests.
import { readdirSync, readFileSync } from "node:fs"
import { join } from "node:path"
import { runsDir } from "./runstore.ts"

export type PlanningRun = { key: string; state: string }
const LIVE = ["running", "testing", "waiting", "queued", "paused", "parked"]
const CLAIMED = /\b([A-Z][A-Z0-9]*-\d+)\s*\(claimed/g

/** {dossier id -> the live planning run that claimed it} for one repo; the first run (by key) wins an id. */
export function planningRuns(repo: string, dir = runsDir()): Record<string, PlanningRun> {
  const out: Record<string, PlanningRun> = {}
  let names: string[] = []
  try { names = readdirSync(dir).filter((n) => n.startsWith(repo + ".") && n.endsWith(".runner.json")).sort() } catch { return out }
  for (const n of names) {
    const key = n.slice(0, -".runner.json".length)
    const short = key.slice(repo.length + 1)
    if (!(short.startsWith("seed-") || /\.plan-D-\d+$/.test(short))) continue
    let st: { command?: string; state?: string } = {}
    try { st = JSON.parse(readFileSync(join(dir, n), "utf8")) || {} } catch { continue }
    if (st.command !== "plan" || !st.state || !LIVE.includes(st.state)) continue
    let text = ""
    try { text = readFileSync(join(dir, `${key}.jsonl`), "utf8") } catch { continue }
    for (const line of text.split("\n")) {
      if (!line.includes('"stage"') || !line.includes("(claimed")) continue
      let o: any
      try { o = JSON.parse(line) } catch { continue }
      if (o?.kind !== "stage") continue
      for (const m of `${o.text ?? ""} ${o.stage ?? ""}`.matchAll(CLAIMED)) out[m[1].toUpperCase()] ??= { key, state: st.state }
    }
  }
  return out
}
