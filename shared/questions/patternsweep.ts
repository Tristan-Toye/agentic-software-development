// The pattern-sweep record per repo: <agentic dir>/pattern-sweep/<repo>.json, {"last_run": iso, "items": N, "summary": "..."},
// written by scripts/pattern_sweep.py record (the /pattern-sweep skill). The agentic dir is ASD_AGENTIC_DIR, else ~/.agentic.
import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

export const STALE_DAYS = 30
export type SweepView = { repo: string; lastRun: string | null; items: number | null; summary: string; days: number | null; stale: boolean }

export const sweepDir = (): string => join(process.env.ASD_AGENTIC_DIR || join(homedir(), ".agentic"), "pattern-sweep")

/** The last sweep of one repo; never run (or an unreadable file) is lastRun null and stale. */
export function sweepOf(repo: string, now = Date.now(), dir = sweepDir()): SweepView {
  const none: SweepView = { repo, lastRun: null, items: null, summary: "", days: null, stale: true }
  if (!/^[\w.-]+$/.test(repo)) return none
  try {
    const j = JSON.parse(readFileSync(join(dir, `${repo}.json`), "utf8"))
    const t = Date.parse(j?.last_run)
    if (!Number.isFinite(t)) return none
    const days = Math.max(0, Math.floor((now - t) / 86400000))
    return { repo, lastRun: j.last_run, items: Number.isInteger(j.items) ? j.items : null, summary: typeof j.summary === "string" ? j.summary : "", days, stale: days > STALE_DAYS }
  } catch { return none }
}
