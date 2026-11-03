// Pipeline discovery: dossiers, stage, session activity, PR/CI. Contract: references/owner-questions.md (Dashboard).
import { closeSync, existsSync, openSync, readdirSync, readFileSync, readSync, statSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import type { Question } from "./store.ts"
import { emitChange } from "./events.ts"
import { blockedByOf, controlsFor, type Controls } from "./controls.ts"
import { readRunner, readUpdates, runnerActive, runsDir, summaryOf, type RunnerState, type Update } from "./runstore.ts"

export const STATUSES = ["planned", "ready", "building", "review", "pr", "done", "dropped"] as const
const TAIL = 256 * 1024
const HEAD = 64 * 1024

export type Front = { pr?: string; id?: string; title?: string; status?: string; jira?: string; updated?: string; created?: string; blocked_by?: string }
export type Session = { state: "active" | "idle" | "none"; lastTs: string; context: number; requests: number; requestsExact: boolean }
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
export type DeferredItem = { id: string; kind: string; title: string; loc: string; risk: string; why: string; state: "open" | "fixed" | "dossier" | "deprecated"; note: string; dossier: string }
export type Deferred = { items: DeferredItem[]; pr: string; report: boolean }

/**
 * The deferred ledger of a build log: `- D-n <kind> <title> — <path:line> — <risk> — <why>`
 * lines (first one per id wins), closed by `DEFERRED-FIXED: D-n <sha>` and routed by
 * `DEFERRED-TRIAGE: D-n → dossier — …`; `DEFERRED-FIX: PR <url>` names the stacked PR.
 */
export function parseDeferred(log: string): { items: DeferredItem[]; pr: string } {
  const items = new Map<string, DeferredItem>()
  let pr = ""
  for (const raw of log.split("\n")) {
    const l = raw.trim()
    const m = l.match(/^[-*]\s+(D-\d+)\s+(\S+)\s+(.*)$/)
    if (m && !items.has(m[1])) {
      const parts = m[3].split(/\s+—\s+/)
      // Older ledgers close an item in place: "- D-1 RESOLVED: …".
      const resolved = /^resolved\b/i.test(m[2])
      items.set(m[1], { id: m[1], kind: resolved ? "resolved" : m[2], title: parts[0] ?? "", loc: parts[1] ?? "", risk: parts[2] ?? "", why: parts.slice(3).join(" — "), state: resolved ? "fixed" : "open", note: "", dossier: "" })
      continue
    }
    const fx = l.match(/^(?:[-*]\s+)?DEFERRED-FIXED:\s*(D-\d+)\s*(\S*)/)
    if (fx && items.has(fx[1])) { const it = items.get(fx[1])!; it.state = "fixed"; it.note = fx[2]; continue }
    const tr = l.match(/^(?:[-*]\s+)?DEFERRED-TRIAGE:\s*(D-\d+)\s*→\s*(direct|dossier)\b\s*(?:—\s*)?(.*)$/)
    if (tr && items.has(tr[1]) && tr[2] === "dossier") { const it = items.get(tr[1])!; if (it.state !== "fixed") { it.state = "dossier"; it.note = tr[3] } continue }
    const dr = l.match(/^(?:[-*]\s+)?DEFERRED-DROPPED:\s*(D-\d+)\s*(?:—\s*)?(.*)$/)
    if (dr && items.has(dr[1])) { const it = items.get(dr[1])!; if (it.state !== "fixed") { it.state = "deprecated"; it.note = dr[2] } continue }
    const dd = l.match(/^(?:[-*]\s+)?DEFERRED-DOSSIER:\s*(D-\d+)\s*→\s*([A-Z][A-Z0-9]*-\d+)/)
    if (dd && items.has(dd[1])) { const it = items.get(dd[1])!; if (it.state !== "fixed") { it.state = "dossier"; it.dossier = dd[2] } continue }
    const p = l.match(/DEFERRED-FIX:\s*PR\s+(\S+)/)
    if (p) pr = p[1]
  }
  return { items: [...items.values()], pr }
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

/** Dossiers per repository: roots are grouped by their main checkout first, because an id is unique only inside one repo. */
export async function pickDossiersByRepo(roots: string[], mainFor: (wt: string) => Promise<string> = mainOf): Promise<{ main: string; front: Front; text: string; file: string; worktree: string }[]> {
  const byMain = new Map<string, string[]>()
  for (const wt of [...new Set(roots)].sort()) {
    const m = await mainFor(wt)
    byMain.set(m, [...(byMain.get(m) ?? []), wt])
  }
  const out: { main: string; front: Front; text: string; file: string; worktree: string }[] = []
  for (const [main, wts] of byMain) for (const d of pickDossiers(wts)) out.push({ ...d, main })
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
export function tailTranscript(path: string): { context: number; requests: number; exact: boolean; lastTs: string } {
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
  return { context, requests: ids.size + anon, exact: start === 0, lastTs }
}

type TInfo = { path: string; cwd: string; mtime: number }
const cwdCache = new Map<string, { mtime: number; cwd: string }>()
export function claudeProjects(): string { return process.env.ASD_CLAUDE_PROJECTS || join(homedir(), ".claude", "projects") }

export function listTranscripts(projects = claudeProjects()): TInfo[] {
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
    const mine = transcripts.filter((t) => t.cwd && (t.cwd === wt || t.cwd.startsWith(wt + "/"))).sort((a, b) => b.mtime - a.mtime)
    let session: Session = { state: "none", lastTs: "", context: 0, requests: 0, requestsExact: true }
    if (mine.length) {
      const t = mine[0]
      let tail = { context: 0, requests: 0, exact: true, lastTs: "" }
      try { tail = tailTranscript(t.path) } catch { /* gone */ }
      session = { state: now - t.mtime < 10 * 60_000 ? "active" : "idle", lastTs: new Date(t.mtime).toISOString(), context: tail.context, requests: tail.requests, requestsExact: tail.exact }
    }
    const repoName = baseName(await mainOf(wt))
    const runner = readRunner(`${repoName}.${f.id}`)
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
      ...(() => { const pd = deferredSince(f.updated ?? "", runner) ? parseDeferred(log) : { items: [], pr: "" }; const deferred: Deferred = { ...pd, report: existsSync(join(runsDir(), `${repoName}.${f.id}.deferred.md`)) }; return { deferred, deferredOpen: pd.items.filter((i) => i.state === "open").length } })(),
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
      const sig = JSON.stringify(v.runs.map((r) => [r.repo, r.id, r.status, r.phase, r.lastActivity, r.session.state, r.pr]))
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
