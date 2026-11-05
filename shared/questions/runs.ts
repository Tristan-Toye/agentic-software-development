// Pipeline discovery: dossiers, stage, session activity, PR/CI. Contract: references/owner-questions.md (Dashboard).
import { closeSync, existsSync, openSync, readdirSync, readFileSync, readSync, statSync } from "node:fs"
import { homedir } from "node:os"
import { basename, join, relative } from "node:path"
import type { Question } from "./store.ts"
import { emitChange } from "./events.ts"
import { blockedByOf, controlsFor, type Controls } from "./controls.ts"
import { readRunner, readUpdates, runnerActive, runsDir, summaryOf, type RunnerState, type Update } from "./runstore.ts"

export const STATUSES = ["planned", "ready", "building", "review", "pr", "done", "dropped"] as const
const TAIL = 256 * 1024
const HEAD = 64 * 1024

export type Front = { pr?: string; id?: string; title?: string; status?: string; jira?: string; updated?: string; created?: string; blocked_by?: string }
export type Session = { state: "active" | "idle" | "none"; lastTs: string; context: number; requests: number; requestsExact: boolean; activity?: Activity }
/**
 * What the orchestrator is doing right now, from its transcript: the tool call still in flight (`doing`, with the
 * Bash description or the agent, workflow or skill it names), the background commands, workflows and agents it
 * launched that have not notified it yet (`background`), and when the transcript last moved (`lastTs`). An empty
 * `doing` with background work means it ended its turn to wait for that work.
 */
export type Activity = { doing: string; doingSince: string; background: { label: string; since: string }[]; lastTs: string }
export type Pr = { number: number; url: string; state: string; green: number; red: number; pending: number }
export type Run = {
  id: string; title: string; status: string; jira: string; updated: string; created: string
  repo: string; repoLabel: string; root: string; worktree: string; file: string
  phase: string; phaseName: string; lastActivity: string; logLines: number
  session: Session; pr: Pr | null; questions: string[]; answered: QA[]
  running: boolean; summary: string; updates: Update[]; runner: RunnerState | null
  main: string; blockedBy: string[]; controls: Controls; view: "run" | "dossier"
  deferred: Deferred; deferredOpen: number; runnerLog: string
}

/**
 * Deferred ledgers count from the new flow on: a dossier last updated before
 * `ASD_DEFERRED_SINCE` (default 2026-11-03, when /fix-deferred and the runner
 * landed) and never run by the runner has items that were most likely handled
 * without the ledger being updated, so they are not shown.
 */
export function deferredSince(updated: string, runner: unknown): boolean {
  return runner != null || updated.slice(0, 10) >= (process.env.ASD_DEFERRED_SINCE || "2026-11-03")
}
/** One deferred issue from a dossier's ledger (`/work-on` Phase 9, `/deferred`, `/fix-deferred`). */
export type DeferredItem = { id: string; kind: string; title: string; loc: string; risk: string; why: string; state: "open" | "fixing" | "fixed" | "dossier" | "deprecated"; note: string; dossier: string; contract?: string; effort?: string; inReport?: boolean; question?: string; ruling?: string; keptOpen?: boolean }
export type Deferred = { items: DeferredItem[]; pr: string; report: boolean; branch?: string }

/**
 * The deferred ledger of a build log: `- D-n <kind> <title> — <path:line> — <risk> — <why>[ — contract: <c>][ — effort: <e>]`
 * lines (first one per id wins; the `contract:` and `effort:` parts may sit anywhere after the title), closed by `DEFERRED-FIXED: D-n <sha>` and routed by
 * `DEFERRED-TRIAGE: D-n → dossier — …`; `DEFERRED-FIX: PR <url>` names the stacked PR.
 */
export function parseDeferred(log: string): { items: DeferredItem[]; pr: string; branch: string } {
  const items = new Map<string, DeferredItem>()
  let pr = "", branch = ""
  const asked = new Map<string, string>() // question id -> D-id
  for (const raw of log.split("\n")) {
    const l = raw.trim()
    const m = l.match(/^[-*]\s+(D-\d+)\s+(\S+)\s+(.*)$/)
    if (m && !items.has(m[1])) {
      const all = m[3].split(/\s+—\s+/)
      const field = (k: string) => all.slice(1).map((p) => p.match(new RegExp(`^${k}:\\s*(\\S.*?)\\s*$`, "i"))?.[1]).find(Boolean) ?? ""
      const contract = field("contract").replace(/[:\s].*$/, "").toLowerCase(), effort = field("effort").replace(/[\s(].*$/, "").toUpperCase()
      const parts = [all[0], ...all.slice(1).filter((p) => !/^(contract|effort):/i.test(p))]
      // Older ledgers close an item in place: "- D-1 RESOLVED: …".
      const resolved = /^resolved\b/i.test(m[2])
      items.set(m[1], { id: m[1], kind: resolved ? "resolved" : m[2], title: parts[0] ?? "", loc: parts[1] ?? "", risk: parts[2] ?? "", why: parts.slice(3).join(" — "), state: resolved ? "fixed" : "open", note: "", dossier: "", ...(contract ? { contract } : {}), ...(effort ? { effort } : {}) })
      continue
    }
    const fx = l.match(/^(?:[-*]\s+)?DEFERRED-FIXED:\s*(D-\d+)\s*(\S*)/)
    if (fx && items.has(fx[1])) { const it = items.get(fx[1])!; it.state = "fixed"; it.note = fx[2]; continue }
    const tr = l.match(/^(?:[-*]\s+)?DEFERRED-TRIAGE:\s*(D-\d+)\s*→\s*(direct|dossier)\b\s*(?:—\s*)?(.*)$/)
    if (tr && items.has(tr[1]) && tr[2] === "dossier") { const it = items.get(tr[1])!; if (it.state !== "fixed") { it.state = "dossier"; it.note = tr[3] } continue }
    // A direct fix under way: `/fix-deferred` triaged it into the stacked PR and has not logged it fixed yet.
    if (tr && items.has(tr[1]) && tr[2] === "direct") { const it = items.get(tr[1])!; if (it.state === "open" || (it.state === "fixing" && it.note.startsWith("your ruling:"))) { it.state = "fixing"; it.note = tr[3] } continue }
    // The questions /fix-deferred put to the owner and the owner's rulings: `DEFERRED-ASKED: D-n <qid>` and
    // `DEFERRED-RULING: D-n — <ruling>`, or the free form `QUESTION: <qid> — D-n …` / `OWNER-RULING …: <qid> — <ruling>`.
    const qa = l.match(/^(?:[-*]\s+)?(?:DEFERRED-ASKED:\s*(D-\d+)\s+(\S+\.Q\d+)|QUESTION:\s*(\S+\.Q\d+)\s*—\s*(D-\d+)\b)/)
    if (qa) { const did = qa[1] ?? qa[4], qid = qa[2] ?? qa[3]; asked.set(qid, did); const it = items.get(did); if (it) it.question = qid; continue }
    const ru = l.match(/^(?:[-*]\s+)?(?:DEFERRED-RULING:\s*(D-\d+)\s*(?:—\s*)?(.*)|OWNER-RULING\b[^:]*:\s*(\S+\.Q\d+)\s*—\s*(.*))$/)
    if (ru) {
      const did = ru[1] ?? asked.get(ru[3] ?? "") ?? (ru[4] ?? "").match(/\bD-\d+\b/)?.[0]
      const text = (ru[2] ?? ru[4] ?? "").replace(/^D-\d+\s*/, "").trim()
      const it = did ? items.get(did) : undefined
      if (it && it.state === "open") {
        it.ruling = text
        // "stays open" / "leave it open" keeps the item for later; any other ruling sets the run to work on it.
        if (/\b(stays? open|leave (?:it )?open|left open|keep (?:it )?open|not now|later)\b/i.test(text)) it.keptOpen = true
        else { it.state = "fixing"; it.note = `your ruling: ${text}` }
      }
      continue
    }
    const br = l.match(/DEFERRED-FIX:\s*branch\s+(\S+)/)
    if (br) { branch = br[1]; continue }
    const dr = l.match(/^(?:[-*]\s+)?DEFERRED-DROPPED:\s*(D-\d+)\s*(?:—\s*)?(.*)$/)
    if (dr && items.has(dr[1])) { const it = items.get(dr[1])!; if (it.state !== "fixed") { it.state = "deprecated"; it.note = dr[2] } continue }
    const dd = l.match(/^(?:[-*]\s+)?DEFERRED-DOSSIER:\s*(D-\d+)\s*→\s*([A-Z][A-Z0-9]*-\d+)/)
    if (dd && items.has(dd[1])) { const it = items.get(dd[1])!; if (it.state !== "fixed") { it.state = "dossier"; it.dossier = dd[2] } continue }
    const p = l.match(/DEFERRED-FIX:\s*PR\s+(\S+)/)
    if (p) pr = p[1]
  }
  return { items: [...items.values()], pr, branch }
}
/**
 * The summary table of a `/deferred` report: `| ID | Kind | Title | Risk | Origin | Contract | Effort |`, columns found
 * by their header names. Per D-id its risk, contract boundary and effort; items missing from it are not product code.
 */
export function parseReportTable(md: string): Map<string, { risk: string; contract: string; effort: string; title: string }> {
  const out = new Map<string, { risk: string; contract: string; effort: string; title: string }>()
  let cols: string[] | null = null
  const cells = (l: string) => l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim())
  for (const l of md.split("\n")) {
    if (!l.trim().startsWith("|")) { cols = null; continue }
    const c = cells(l)
    if (!cols) { if (c.some((x) => /^id$/i.test(x))) cols = c.map((x) => x.toLowerCase()); continue }
    if (c.every((x) => /^:?-+:?$/.test(x))) continue
    const at = (n: string) => { const i = cols!.indexOf(n); return i >= 0 ? (c[i] ?? "").replace(/`/g, "") : "" }
    const id = at("id")
    if (/^D-\d+$/.test(id)) out.set(id, { risk: at("risk"), contract: at("contract"), effort: at("effort"), title: at("title") })
  }
  return out
}
/** The ledger's items with the report's contract and effort, when the run stored a report; an item the report lacks keeps its ledger values. */
export function withReport(items: DeferredItem[], report: string): DeferredItem[] {
  if (!report) return items
  const t = parseReportTable(report)
  if (!t.size) return items
  return items.map((i) => { const r = t.get(i.id); return r ? { ...i, contract: r.contract.replace(/[:\s].*$/, "") || i.contract, effort: r.effort || i.effort, inReport: true } : { ...i, inReport: false } })
}
/** An answered question, as shown under the dossier it came from. */
export type QA = { id: string; text: string; answer: string; option: string; asked: string; answeredAt: string }
export type RunsView = { scannedAt: string; runs: Run[] }

/** Statuses of a dossier that is in flight: not planned, ready, done or dropped. */
export const RUNNING = ["building", "review", "pr"]
export const isRunning = (status: string): boolean => RUNNING.includes(status)

/** `{ "4": "Fan out: concurrent and blind" }` from the first heading of each `phase-<n>.md`. */
export function loadPhaseNames(dir = join(import.meta.dir, "..", "..", "references", "work-on")): Record<string, string> {
  const out: Record<string, string> = {}
  let names: string[] = []
  try { names = readdirSync(dir) } catch { return out }
  for (const n of names) {
    const m = n.match(/^phase-(\d+)\.md$/)
    if (!m) continue
    try {
      const h = readFileSync(join(dir, n), "utf8").split("\n").find((l) => /^#{1,6}\s/.test(l))
      if (h) out[m[1]] = h.replace(/^#+\s*/, "").replace(/^Phase\s*\d+\s*[—–:-]\s*/i, "").trim()
    } catch { /* unreadable */ }
  }
  return out
}
export const PHASE_NAMES = loadPhaseNames()
/** Name of a phase hit such as "4" or "4a"; "plan" is the planning stage. */
export function phaseNameOf(names: Record<string, string>, phase: string): string {
  if (phase === "plan") return "Plan"
  return names[phase.replace(/[a-z]$/, "")] ?? ""
}

/** Simple `key: value` scalars between the first two `---` lines. */
export function parseFrontMatter(text: string): Front {
  const lines = text.split("\n")
  if (lines[0]?.trim() !== "---") return {}
  const out: Record<string, string> = {}
  for (let i = 1; i < lines.length && lines[i].trim() !== "---"; i++) {
    const m = lines[i].match(/^([A-Za-z_][\w-]*):\s*(.*?)\s*$/)
    if (!m) continue
    let v = m[2]
    if ((v.startsWith('"') && v.endsWith('"') || v.startsWith("'") && v.endsWith("'")) && v.length >= 2) v = v.slice(1, -1)
    out[m[1]] = v
  }
  return out
}

export function buildLog(text: string): string {
  const i = text.indexOf("## Build log")
  return i < 0 ? "" : text.slice(i)
}
/** Same measure as scripts/budget_report.py: newline count of the text from `## Build log`; -1 if absent. */
export function logLength(text: string): number {
  const i = text.indexOf("## Build log")
  return i < 0 ? -1 : text.slice(i).split("\n").length - 1
}
/**
 * The phase a build log says it reached: the last line that *starts* with
 * `Phase <n>` or `PHASE <n>` (a bullet's marker allowed), the way /work-on
 * records a phase outcome. A phase named mid-sentence — "a draft PR at Phase 6"
 * in a ruling — is a plan, not progress, and does not count.
 */
export function lastPhase(log: string): string {
  let last = ""
  for (const m of log.matchAll(/^\s*(?:[-*]\s+)?(?:\*\*)?(?:Phase|PHASE)\s+(\d+[a-z]?)\b/gm)) last = m[1]
  return last
}
/** The phase of the newest `stage` line in a run's updates (checkpoint names start `Phase <n>`). */
export function stagePhase(updates: { kind: string; stage?: string; text: string }[]): string {
  for (const u of updates) if (u.kind === "stage") { const m = `${u.stage ?? ""} ${u.text}`.match(/\b(?:Phase|PHASE)\s+(\d+[a-z]?)\b/); if (m) return m[1] }
  return ""
}
/** The last build-log entry, whole: a wrapped bullet is joined back, a trailing fenced block is skipped. */
export function lastLine(log: string): string {
  let ls = log.split("\n").map((l) => l.trim()).filter(Boolean).slice(1) // drop the "## Build log" heading
  if (ls.length && ls[ls.length - 1].startsWith("```")) {
    const open = ls.slice(0, -1).map((l) => l.startsWith("```")).lastIndexOf(true)
    ls = open >= 0 ? ls.slice(0, open) : ls
  }
  const isStart = (l: string) => /^(- |\* |#{2,4} |[A-Z][A-Z0-9-]+(\s*\([^)]*\))?:)/.test(l)
  let i = ls.length - 1
  while (i > 0 && !isStart(ls[i])) i--
  const l = ls.slice(Math.max(i, 0)).join(" ").replace(/^(- |\* |#{2,4} )/, "")
  return l.length > 160 ? l.slice(0, 159) + "…" : l
}

/** Dossier files under one root, each parsed. */
export function dossiersIn(root: string): { front: Front; text: string; file: string }[] {
  const dir = join(root, ".discovery", "dossiers")
  let names: string[] = []
  try { names = readdirSync(dir).filter((n) => n.endsWith(".md")).sort() } catch { return [] }
  const out: { front: Front; text: string; file: string }[] = []
  for (const n of names) {
    try {
      const text = readFileSync(join(dir, n), "utf8")
      const front = parseFrontMatter(text)
      if (!front.id) front.id = n.replace(/\.md$/, "").replace(/^([A-Za-z]+-\d+).*$/, "$1")
      out.push({ front, text, file: join(dir, n) })
    } catch { /* unreadable: skip */ }
  }
  return out
}

/** Per id, the copy with the longest build log wins (ties: first in sorted worktree order). */
export function pickDossiers(roots: string[]): { front: Front; text: string; file: string; worktree: string }[] {
  const best = new Map<string, { front: Front; text: string; file: string; worktree: string; len: number }>()
  for (const wt of [...new Set(roots)].sort()) {
    for (const d of dossiersIn(wt)) {
      const len = logLength(d.text)
      const cur = best.get(d.front.id!)
      if (!cur || len > cur.len) best.set(d.front.id!, { ...d, worktree: wt, len })
    }
  }
  return [...best.values()]
}

// ---- a finished build's copy on its own branch ----
// A finished /work-on removes its worktree but keeps its branch: until the PR merges, the dossier's `done`
// status and its ledger live only there. Such a copy stands in `file` as `git:<main>::<ref>::<path>`.
const GIT_FILE = /^git:(.*?)::(.*?)::(.*)$/
const NOT_BUILD = /^(?:origin\/)?(?:plan|stub)\/|^origin\/HEAD$|^(?:origin\/)?(?:main|master|development|production)$/
const blobs = new Map<string, string>()
export const branchFile = (main: string, ref: string, rel: string): string => `git:${main}::${ref}::${rel}`
/** A dossier's text: from disk, or from the branch a `git:` file names. Throws when unreadable. */
export function readDossier(file: string): string {
  const m = file.match(GIT_FILE)
  if (!m) return readFileSync(file, "utf8")
  const p = Bun.spawnSync(["git", "-C", m[1], "show", `${m[2]}:${m[3]}`], { stdout: "pipe", stderr: "ignore" })
  if (p.exitCode !== 0) throw new Error(`cannot read ${m[3]} on ${m[2]}`)
  return p.stdout.toString()
}
/** Whether a branch name is the build branch of dossier `id`: the id as a whole token, any case; plan and stub branches never are. */
export function isBuildBranch(ref: string, id: string): boolean {
  return !NOT_BUILD.test(ref) && new RegExp(`(?:^|[/_-])${id.replace(/[^\w-]/g, "")}(?:$|[-_/])`, "i").test(ref)
}
/**
 * Each picked dossier, replaced by its copy on its own build branch (local or `origin/`) when that copy's build log is
 * longer. `git` errors leave the pick as it is.
 */
export async function withBranchCopies<T extends { front: Front; text: string; file: string; worktree: string }>(main: string, picked: T[]): Promise<(T & { branch?: string })[]> {
  if (!existsSync(join(main, ".git"))) return picked
  const refs = (await sh(["git", "-C", main, "for-each-ref", "--format=%(objectname) %(refname:short)", "refs/heads", "refs/remotes/origin"], undefined, 5000))
    .split("\n").map((l) => l.trim().split(" ")).filter((p) => p.length === 2 && !NOT_BUILD.test(p[1]))
  if (!refs.length) return picked
  const out: (T & { branch?: string })[] = []
  for (const d of picked) {
    let best: (T & { branch?: string }) = d
    let bestLen = logLength(d.text)
    const rel = relative(main, d.file).startsWith("..") ? join(".discovery", "dossiers", basename(d.file)) : relative(main, d.file)
    for (const [sha, ref] of refs) {
      if (!isBuildBranch(ref, d.front.id!)) continue
      const k = `${sha}:${rel}`
      let text = blobs.get(k)
      if (text === undefined) {
        text = await sh(["git", "-C", main, "show", `${sha}:${rel}`], undefined, 5000)
        if (blobs.size > 2000) blobs.clear()
        blobs.set(k, text)
      }
      if (!text) continue
      const front = parseFrontMatter(text)
      if ((front.id ?? d.front.id) !== d.front.id) continue
      const len = logLength(text)
      if (len > bestLen) { bestLen = len; best = { ...d, front: { ...front, id: d.front.id }, text, file: branchFile(main, ref, rel), branch: ref } }
    }
    out.push(best)
  }
  return out
}

/** Dossiers per repository: roots are grouped by their main checkout first, because an id is unique only inside one repo. */
export async function pickDossiersByRepo(roots: string[], mainFor: (wt: string) => Promise<string> = mainOf): Promise<{ main: string; front: Front; text: string; file: string; worktree: string }[]> {
  const byMain = new Map<string, string[]>()
  for (const wt of [...new Set(roots)].sort()) {
    const m = await mainFor(wt)
    byMain.set(m, [...(byMain.get(m) ?? []), wt])
  }
  const out: { main: string; front: Front; text: string; file: string; worktree: string }[] = []
  for (const [main, wts] of byMain) for (const d of await withBranchCopies(main, pickDossiers(wts))) out.push({ ...d, main })
  return out
}

/** The repo a question belongs to: among the runs with its dossier id, the one named by `q.repo` or whose checkouts contain `q.root`; the only candidate otherwise. */
export function questionRepo(q: { repo: string; dossier: string; root?: string }, runs: { repo: string; id: string; main: string; worktree: string }[]): string | undefined {
  const c = runs.filter((r) => r.id === q.dossier)
  const inside = (p: string, d: string) => !!p && !!d && (p === d || p.startsWith(d + "/"))
  const hit = c.find((r) => r.repo === q.repo) ?? c.find((r) => inside(q.root ?? "", r.worktree) || inside(q.root ?? "", r.main))
  if (hit) return hit.repo
  return c.length === 1 ? c[0].repo : undefined
}

/** Whether a question was asked in the repo `repo` whose main checkout is `main` (named by `q.repo`, or its root is inside the checkout or a sibling worktree `<main>-...`). */
export function questionInRepo(q: { repo: string; root?: string }, repo: string, main: string): boolean {
  if (q.repo === repo) return true
  const r = q.root ?? ""
  return !!r && (r === main || r.startsWith(main + "/") || r.startsWith(main + "-"))
}

/** Which view a dossier route opens: done or dropped show the dossier; running, in progress, or any with run updates show the run. */
export function viewFor(status: string, running: boolean, updates: number): "run" | "dossier" {
  if (status === "done" || status === "dropped") return "dossier"
  return running || isRunning(status) || updates > 0 ? "run" : "dossier"
}

// ---- transcripts ----
function readRange(path: string, start: number, len: number): string {
  const fd = openSync(path, "r")
  try {
    const buf = Buffer.alloc(len)
    const n = readSync(fd, buf, 0, len, start)
    return buf.subarray(0, n).toString("utf8")
  } finally { closeSync(fd) }
}

/** The `cwd` of the first line of a transcript that has one (head only). */
export function transcriptCwd(path: string): string {
  try {
    const size = statSync(path).size
    for (const l of readRange(path, 0, Math.min(size, HEAD)).split("\n")) {
      if (!l.includes('"cwd"')) continue
      try { const o = JSON.parse(l); if (typeof o.cwd === "string" && o.cwd) return o.cwd } catch { /* partial line */ }
    }
  } catch { /* gone */ }
  return ""
}

/** Context size and request count from the tail of a transcript. Requests are counted over the tail only unless the file fits it. */
export function tailTranscript(path: string): { context: number; requests: number; exact: boolean; lastTs: string; activity: Activity } {
  const size = statSync(path).size
  const start = Math.max(0, size - TAIL)
  let lines = readRange(path, start, size - start).split("\n")
  if (start > 0) lines = lines.slice(1) // first line is cut
  let context = 0
  let lastTs = ""
  const ids = new Set<string>()
  let anon = 0
  for (let i = lines.length - 1; i >= 0; i--) {
    if (!lines[i].includes('"assistant"')) continue
    let o: any
    try { o = JSON.parse(lines[i]) } catch { continue }
    if (o?.type !== "assistant" || !o.message) continue
    const id = o.message.id
    if (id) ids.add(id); else anon++
    const u = o.message.usage
    if (!context && u) {
      context = (u.input_tokens ?? 0) + (u.cache_read_input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0)
      lastTs = o.timestamp ?? ""
    }
  }
  return { context, requests: ids.size + anon, exact: start === 0, lastTs, activity: activityOf(lines) }
}

const clip = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s)
/** A tool call's one-line label: its description, else the agent, workflow or skill it names, else the tool. */
export function toolLabel(name: string, input: any): string {
  const i = input ?? {}
  if (typeof i.description === "string" && i.description.trim()) return clip(i.description.trim(), 120)
  if (name === "Workflow") return "workflow " + String(i.name ?? i.scriptPath ?? "").split("/").pop()
  if (name === "Skill") return "skill " + String(i.skill ?? "").replace(/^.*:/, "")
  if (name === "Agent" || name === "Task") return "agent " + String(i.subagent_type ?? "").replace(/^.*:/, "")
  if (typeof i.file_path === "string") return name + " " + i.file_path.split("/").pop()
  return name.replace(/^mcp__[^_]+(?:_[^_]+)*?__/, "")
}
const BG_RE = /running in background|launched in background|moved to the background|launched successfully|running in the background/i
/** The orchestrator's activity, read from the transcript lines given (oldest first). */
export function activityOf(lines: string[]): Activity {
  const open = new Map<string, { label: string; since: string }>()
  const bg = new Map<string, { label: string; since: string }>()
  let lastTs = ""
  for (const line of lines) {
    let o: any
    try { o = JSON.parse(line) } catch { continue }
    const ts = typeof o?.timestamp === "string" ? o.timestamp : ""
    const c = o?.message?.content
    if (o?.type === "assistant" && Array.isArray(c)) {
      if (ts) lastTs = ts
      for (const b of c) if (b?.type === "tool_use" && b.id) open.set(b.id, { label: toolLabel(String(b.name ?? ""), b.input), since: ts })
    } else if (o?.type === "user") {
      if (ts) lastTs = ts
      const texts: string[] = typeof c === "string" ? [c] : []
      if (Array.isArray(c)) for (const b of c) {
        if (b?.type === "tool_result" && b.tool_use_id) {
          const t = typeof b.content === "string" ? b.content : JSON.stringify(b.content ?? "")
          const was = open.get(b.tool_use_id)
          open.delete(b.tool_use_id)
          if (was && BG_RE.test(t.slice(0, 400))) bg.set(b.tool_use_id, was)
        } else if (b?.type === "text" && typeof b.text === "string") texts.push(b.text)
      }
      for (const t of texts) for (const m of t.matchAll(/<task-notification>[\s\S]*?<tool-use-id>([^<\s]+)<\/tool-use-id>/g)) bg.delete(m[1])
    }
  }
  const last = [...open.values()].pop()
  return { doing: last?.label ?? "", doingSince: last?.since ?? "", background: [...bg.values()], lastTs }
}

type TInfo = { path: string; cwd: string; mtime: number }
const cwdCache = new Map<string, { mtime: number; cwd: string }>()
export function claudeProjects(): string { return process.env.ASD_CLAUDE_PROJECTS || join(homedir(), ".claude", "projects") }
/** Every account's projects dir: the default one plus each config dir in ~/.agentic/accounts.json. */
export function allClaudeProjects(): string[] {
  if (process.env.ASD_CLAUDE_PROJECTS) return [process.env.ASD_CLAUDE_PROJECTS]
  const out = [claudeProjects()]
  try {
    const j = JSON.parse(readFileSync(join(homedir(), ".agentic", "accounts.json"), "utf8"))
    for (const a of j.accounts ?? []) if (typeof a.dir === "string") { const p = join(a.dir.replace(/^~(?=\/|$)/, homedir()), "projects"); if (!out.includes(p)) out.push(p) }
  } catch { /* one account */ }
  return out
}

export function listTranscripts(projects?: string): TInfo[] {
  if (projects === undefined) return allClaudeProjects().flatMap((p) => listTranscripts(p))
  const out: TInfo[] = []
  let dirs: string[] = []
  try { dirs = readdirSync(projects) } catch { return out }
  for (const d of dirs) {
    let names: string[] = []
    try { names = readdirSync(join(projects, d)).filter((n) => n.endsWith(".jsonl")) } catch { continue }
    for (const n of names) {
      const path = join(projects, d, n)
      try {
        const mtime = statSync(path).mtimeMs
        const c = cwdCache.get(path)
        const cwd = c && c.mtime === mtime ? c.cwd : transcriptCwd(path)
        cwdCache.set(path, { mtime, cwd })
        out.push({ path, cwd, mtime })
      } catch { /* gone */ }
    }
  }
  return out
}

// ---- roots ----
export async function sh(cmd: string[], cwd: string | undefined, timeoutMs: number): Promise<string> {
  try {
    const p = Bun.spawn(cmd, { cwd, stdout: "pipe", stderr: "ignore", stdin: "ignore" })
    const t = setTimeout(() => p.kill(), timeoutMs)
    const out = await new Response(p.stdout).text()
    clearTimeout(t)
    return (await p.exited) === 0 ? out : ""
  } catch { return "" }
}

export async function worktreesOf(root: string): Promise<string[]> {
  if (!existsSync(join(root, ".git"))) return [root].filter((r) => existsSync(r))
  const out = await sh(["git", "-C", root, "worktree", "list", "--porcelain"], undefined, 5000)
  const wts = out.split("\n").filter((l) => l.startsWith("worktree ")).map((l) => l.slice(9))
  return wts.length ? wts : [root]
}

export async function candidateRoots(questions: Question[], transcripts: TInfo[]): Promise<string[]> {
  const set = new Set<string>()
  if (process.env.ASD_RUNS_ROOTS) {
    for (const r of process.env.ASD_RUNS_ROOTS.split(":")) if (r) set.add(r)
  } else {
    // newest transcript per project dir -> its cwd
    const newest = new Map<string, TInfo>()
    for (const t of transcripts) {
      const d = t.path.slice(0, t.path.lastIndexOf("/"))
      const c = newest.get(d)
      if (!c || t.mtime > c.mtime) newest.set(d, t)
    }
    for (const t of newest.values()) if (t.cwd) set.add(t.cwd)
    try {
      const j = JSON.parse(readFileSync(join(homedir(), ".agentic", "repos.json"), "utf8"))
      for (const r of j.roots ?? []) if (typeof r === "string") set.add(r)
    } catch { /* optional */ }
  }
  for (const q of questions) if (q.root) set.add(q.root)
  const all = new Set<string>()
  for (const r of set) for (const w of await worktreesOf(r)) all.add(w)
  return [...all].filter((w) => existsSync(join(w, ".discovery", "dossiers")))
}

export const baseName = (p: string): string => p.split("/").filter(Boolean).pop() ?? p
/**
 * The name shown for a repo: its basename, except a root that is not a git checkout (it only holds `.discovery`,
 * like `~/Documents/Twipe/repos`), which shows its path relative to the home directory. The basename stays the
 * repo's identity in routes, keys and question matching.
 */
export function repoLabel(main: string, home = homedir()): string {
  if (existsSync(join(main, ".git"))) return baseName(main)
  return main.startsWith(home + "/") ? main.slice(home.length + 1) : baseName(main)
}
const mainCache = new Map<string, string>()
/** The main checkout a worktree belongs to (itself when it is one, or not a git checkout). */
export async function mainOf(wt: string): Promise<string> {
  const c = mainCache.get(wt)
  if (c) return c
  let main = wt
  if (existsSync(join(wt, ".git"))) {
    const common = (await sh(["git", "-C", wt, "rev-parse", "--path-format=absolute", "--git-common-dir"], undefined, 5000)).trim()
    if (common.endsWith("/.git")) main = common.slice(0, -5)
  }
  mainCache.set(wt, main)
  return main
}
/** Answered questions grouped by dossier id, for the view's "Q&A" expanders. */
export function answeredBy(questions: Question[], inRepo: (q: Question) => boolean = () => true): Map<string, QA[]> {
  const m = new Map<string, QA[]>()
  for (const q of questions) {
    if (q.state !== "answered" || !inRepo(q)) continue
    const a: QA = { id: q.id, text: q.text, answer: q.answer?.text ?? "", option: q.answer?.option as string ?? "", asked: q.ts, answeredAt: q.answer?.ts ?? "" }
    m.set(q.dossier, [...(m.get(q.dossier) ?? []), a])
  }
  return m
}

// ---- PR / CI ----
const prCache = new Map<string, { at: number; pr: Pr | null }>()
export async function prFor(worktree: string): Promise<Pr | null> {
  if (process.env.ASD_RUNS_NO_GH === "1") return null
  const c = prCache.get(worktree)
  if (c && Date.now() - c.at < 120_000) return c.pr
  let pr: Pr | null = null
  const branch = (await sh(["git", "-C", worktree, "rev-parse", "--abbrev-ref", "HEAD"], undefined, 5000)).trim()
  if (branch && branch !== "HEAD") {
    const out = await sh(["gh", "pr", "view", branch, "--json", "number,url,state,statusCheckRollup"], worktree, 10_000)
    try {
      const j = JSON.parse(out)
      let green = 0, red = 0, pending = 0
      for (const k of j.statusCheckRollup ?? []) {
        const v = String(k.conclusion || k.state || k.status || "").toUpperCase()
        if (["SUCCESS", "NEUTRAL", "SKIPPED"].includes(v)) green++
        else if (["FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"].includes(v)) red++
        else pending++
      }
      pr = { number: j.number, url: j.url, state: j.state, green, red, pending }
    } catch { /* no PR or gh failed: omitted */ }
  }
  prCache.set(worktree, { at: Date.now(), pr })
  return pr
}

// ---- assemble ----
export async function buildRuns(questions: Question[], now = Date.now()): Promise<RunsView> {
  const transcripts = listTranscripts()
  const roots = await candidateRoots(questions, transcripts)
  const runs: Run[] = []
  for (const d of await pickDossiersByRepo(roots)) {
    const f = d.front
    const log = buildLog(d.text)
    const status = f.status ?? "planned"
    // The run's own stage lines (newest first) outrank the build log's text.
    const phaseHit = stagePhase(readUpdates(`${baseName(d.main)}.${f.id}`)) || lastPhase(log)
    const phase = phaseHit || (status === "planned" || status === "ready" ? "plan" : "")
    const wt = d.worktree
    const repoName = baseName(await mainOf(wt))
    const runner = readRunner(`${repoName}.${f.id}`)
    // A live runner names its session: that transcript, wherever it runs (a /fix-deferred run works from the main
    // checkout, not the dossier's worktree). Otherwise the newest transcript working in the worktree.
    const own = runner?.session && runnerActive(runner) ? transcripts.filter((t) => t.path.endsWith(`/${runner.session}.jsonl`)) : []
    const mine = own.length ? own : transcripts.filter((t) => t.cwd && (t.cwd === wt || t.cwd.startsWith(wt + "/"))).sort((a, b) => b.mtime - a.mtime)
    let session: Session = { state: "none", lastTs: "", context: 0, requests: 0, requestsExact: true }
    if (mine.length) {
      const t = mine[0]
      let tail: ReturnType<typeof tailTranscript> | null = null
      try { tail = tailTranscript(t.path) } catch { /* gone */ }
      session = { state: now - t.mtime < 10 * 60_000 ? "active" : "idle", lastTs: new Date(t.mtime).toISOString(), context: tail?.context ?? 0, requests: tail?.requests ?? 0, requestsExact: tail?.exact ?? true, ...(tail ? { activity: tail.activity } : {}) }
    }
    const main = d.main
    const pr = isRunning(status) || runnerActive(runner) ? await prFor(wt) : null
    const qs: string[] = []
    runs.push({
      id: f.id!, title: f.title ?? "", status, jira: f.jira ?? "", updated: f.updated ?? "", created: f.created ?? "",
      repo: repoName, repoLabel: repoLabel(d.main), root: d.worktree, worktree: wt, file: d.file,
      phase, phaseName: phaseNameOf(PHASE_NAMES, phase), lastActivity: lastLine(log), logLines: Math.max(0, logLength(d.text)), session, pr, questions: qs, answered: [],
      running: isRunning(status) || runnerActive(runner), summary: summaryOf(d.text), updates: readUpdates(`${repoName}.${f.id}`), runner,
      main, blockedBy: blockedByOf(f.blocked_by), controls: controlsFor({ status, blockedBy: blockedByOf(f.blocked_by), runner }, () => undefined), view: "dossier",
      runnerLog: runner ? join(runsDir(), `${repoName}.${f.id}.runner.log`) : "",
      ...(() => {
        const pd = deferredSince(f.updated ?? "", runner) ? parseDeferred(log) : { items: [], pr: "", branch: "" }
        let report = ""
        try { report = readFileSync(join(runsDir(), `${repoName}.${f.id}.deferred.md`), "utf8") } catch { /* none */ }
        const deferred: Deferred = { items: withReport(pd.items, report), pr: pd.pr, report: report !== "", branch: pd.branch }
        return { deferred, deferredOpen: pd.items.filter((i) => i.state === "open").length }
      })(),
    })
  }
  return { scannedAt: new Date(now).toISOString(), runs }
}
/** Questions linked to runs of their own repo only; `open` ids and `answered` Q&A per run. */
export function linkQuestions(runs: Run[], questions: Question[]): Map<string, { open: string[]; answered: QA[] }> {
  const out = new Map<string, { open: string[]; answered: QA[] }>()
  const key = (repo: string, id: string) => `${repo}/${id}`
  for (const r of runs) out.set(key(r.repo, r.id), { open: [], answered: [] })
  for (const q of questions) {
    const repo = questionRepo(q, runs)
    const slot = repo === undefined ? undefined : out.get(key(repo, q.dossier))
    if (!slot) continue
    if (q.state === "open") slot.open.push(q.id)
    else for (const a of answeredBy([q]).get(q.dossier) ?? []) slot.answered.push(a)
  }
  return out
}

let cached: RunsView = { scannedAt: "", runs: [] }
let inflight: Promise<void> | null = null
let lastScan = 0
let lastSig = ""
/** Updates and runner state are cheap local reads, so they are fresh on every request. */
function liveStore(r: Run): Pick<Run, "updates" | "runner" | "running"> {
  const key = `${r.repo}.${r.id}`
  const runner = readRunner(key)
  return { updates: readUpdates(key), runner, running: isRunning(r.status) || runnerActive(runner) }
}
/** Cached view; a rescan starts in the background when older than 30 s. */
export async function getRuns(questions: () => Question[]): Promise<RunsView> {
  if (!inflight && Date.now() - lastScan > 30_000) {
    lastScan = Date.now()
    inflight = buildRuns(questions()).then((v) => {
      const sig = JSON.stringify(v.runs.map((r) => [r.repo, r.id, r.status, r.phase, r.lastActivity, r.session.state, r.pr, r.session.activity?.doing ?? "", r.session.activity?.background.length ?? 0]))
      cached = v
      if (sig !== lastSig) { lastSig = sig; emitChange("runs") }
      emitChange("scan")
    }).catch(() => {}).finally(() => { inflight = null })
  }
  if (!cached.scannedAt && inflight) await inflight
  const linked = linkQuestions(cached.runs, questions())
  const live = cached.runs.map((r) => {
    const l = liveStore(r), qa = linked.get(`${r.repo}/${r.id}`)
    return { ...r, ...l, questions: qa?.open ?? [], answered: qa?.answered ?? [], view: viewFor(r.status, l.running, l.updates.length) }
  })
  const statusOf = (repo: string) => (id: string) => live.find((x) => x.repo === repo && x.id === id)?.status
  return { ...cached, runs: live.map((r) => ({ ...r, controls: controlsFor(r, statusOf(r.repo)) })) }
}
