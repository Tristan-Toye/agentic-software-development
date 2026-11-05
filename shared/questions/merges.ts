// The merge detector: a dossier's pull request merged into an open stub starts the /after-merge cleanup run.
// Pure selection here; the server polls GitHub and starts the runner. Contract: references/owner-questions.md (Dashboard).
import { readFileSync, renameSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { runsDir } from "./runstore.ts"

export type MergedPr = { number: number; headRefName: string; mergedAt: string; base: string }
/** Per repo, every merged PR into a stub the detector has seen: `baseline` (merged before it first looked), `pending` (its dossier's runner was busy), `started`. */
export type MergeSeen = Record<string, { id: string; base: string; mergedAt: string; state: "baseline" | "pending" | "started"; at: string }>

/** `ASD_MERGE_DETECTOR=0` turns it off. */
export const mergeDetectorEnabled = (): boolean => process.env.ASD_MERGE_DETECTOR !== "0"
export const mergesFile = (repo: string, dir = runsDir()): string => join(dir, `${repo}.merges.json`)
export function readMergeSeen(repo: string, dir = runsDir()): MergeSeen | null {
  try { return JSON.parse(readFileSync(mergesFile(repo, dir), "utf8")) } catch { return null }
}
export function writeMergeSeen(repo: string, seen: MergeSeen, dir = runsDir()): void {
  const f = mergesFile(repo, dir)
  writeFileSync(f + ".tmp", JSON.stringify(seen, null, 1))
  renameSync(f + ".tmp", f)
}

/** The dossier a merged PR is: its `pr:` number, else the id named in the head branch (any case); a `plan/…` head is none. */
export function dossierOfPr(p: MergedPr, dossiers: { id: string; pr?: string }[]): string | undefined {
  if (/^plan\//.test(p.headRefName)) return undefined
  const n = (d: { pr?: string }) => { const m = (d.pr ?? "").match(/(\d+)\s*$/); return m ? Number(m[1]) : null }
  return (dossiers.find((d) => n(d) === p.number) ?? dossiers.find((d) => new RegExp(`(^|[^A-Za-z0-9])${d.id.replace(/[^\w-]/g, "")}(?![\\w])`, "i").test(p.headRefName)))?.id
}

/**
 * What to start: merged PRs not seen before become `pending`; on the first look (`seen` null) every merged PR is a
 * `baseline`, so old merges never start a run. Each dossier with pending PRs and no live runner gets one run, with
 * all its pending PR numbers; a busy dossier stays pending for the next poll.
 */
export function nextMerges(seen: MergeSeen | null, found: MergedPr[], dossiers: { id: string; pr?: string }[], busy: (id: string) => boolean, now: string):
  { seen: MergeSeen; start: { id: string; prs: number[] }[] } {
  const first = seen === null
  const out: MergeSeen = { ...(seen ?? {}) }
  for (const p of found) {
    const k = String(p.number)
    if (out[k]) continue
    const id = dossierOfPr(p, dossiers)
    if (!id) continue
    out[k] = { id, base: p.base, mergedAt: p.mergedAt, state: first ? "baseline" : "pending", at: now }
  }
  const byId = new Map<string, number[]>()
  for (const [k, v] of Object.entries(out)) if (v.state === "pending") byId.set(v.id, [...(byId.get(v.id) ?? []), Number(k)])
  const start: { id: string; prs: number[] }[] = []
  for (const [id, prs] of byId) {
    if (busy(id)) continue
    prs.sort((a, b) => a - b)
    start.push({ id, prs })
    for (const n of prs) out[String(n)] = { ...out[String(n)], state: "started", at: now }
  }
  return { seen: out, start }
}
