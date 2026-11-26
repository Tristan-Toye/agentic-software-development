// The trust-report indicator, per repository: scripts/trust_report.py writes <agentic>/trust-report/<repo>.json
// ({"last_run","stubs","prs","flagged"}) on every run; this only reads it. Missing or unreadable = never run.
import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

export type TrustView = { repo: string; lastRun: string | null; daysAgo: number | null; flagged: number; prs: number; stubs: number; alarm: boolean }
export const TRUST_ALARM_DAYS = 14

export const trustDir = (): string => join(process.env.ASD_AGENTIC_DIR || join(homedir(), ".agentic"), "trust-report")

export function trustOf(repo: string, now = Date.now(), dir = trustDir()): TrustView {
  const none: TrustView = { repo, lastRun: null, daysAgo: null, flagged: 0, prs: 0, stubs: 0, alarm: true }
  try {
    const j = JSON.parse(readFileSync(join(dir, `${repo}.json`), "utf8"))
    const t = Date.parse(String(j.last_run))
    if (!Number.isFinite(t)) return none
    const daysAgo = Math.max(0, Math.floor((now - t) / 86400000))
    return { repo, lastRun: String(j.last_run), daysAgo, flagged: Number(j.flagged) || 0, prs: Number(j.prs) || 0, stubs: Number(j.stubs) || 0, alarm: daysAgo > TRUST_ALARM_DAYS }
  } catch { return none }
}

export const trustViews = (repos: string[], now = Date.now(), dir = trustDir()): TrustView[] => repos.map((r) => trustOf(r, now, dir))
