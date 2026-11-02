// Pipeline "Runs" discovery: dossiers, phase, session activity, PR/CI. Contract: references/owner-questions.md (Runs).
import { closeSync, existsSync, openSync, readdirSync, readFileSync, readSync, statSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import type { Question } from "./store.ts"

export const STATUSES = ["planned", "ready", "building", "review", "pr", "done", "dropped"] as const
const TAIL = 256 * 1024
const HEAD = 64 * 1024

export type Front = { id?: string; title?: string; status?: string; jira?: string; updated?: string; created?: string }
export type Session = { state: "active" | "idle" | "none"; lastTs: string; context: number; requests: number; requestsExact: boolean }
export type Pr = { number: number; url: string; state: string; green: number; red: number; pending: number }
export type Run = {
  id: string; title: string; status: string; jira: string; updated: string; created: string
  repo: string; root: string; worktree: string; file: string
  phase: string; lastActivity: string; logLines: number
  session: Session; pr: Pr | null; questions: string[]
}
export type RunsView = { scannedAt: string; runs: Run[] }

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
export function lastPhase(log: string): string {
  let last = ""
  for (const m of log.matchAll(/\bPhase (\d+[a-z]?)\b|\bPHASE (\d+[a-z]?)\b/g)) last = m[1] ?? m[2]
  return last
}
export function lastLine(log: string): string {
  const ls = log.split("\n").map((l) => l.trim()).filter(Boolean)
  const l = ls.length > 1 ? ls[ls.length - 1] : ""
  return l.length > 160 ? l.slice(0, 160) : l
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
async function sh(cmd: string[], cwd: string | undefined, timeoutMs: number): Promise<string> {
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
  for (const d of pickDossiers(roots)) {
    const f = d.front
    const log = buildLog(d.text)
    const status = f.status ?? "planned"
    const phaseHit = lastPhase(log)
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
    const pr = ["building", "review", "pr"].includes(status) ? await prFor(wt) : null
    const qs = questions.filter((q) => q.state === "open" && q.dossier === f.id).map((q) => q.id)
    runs.push({
      id: f.id!, title: f.title ?? "", status, jira: f.jira ?? "", updated: f.updated ?? "", created: f.created ?? "",
      repo: wt.split("/").filter(Boolean).pop() ?? wt, root: d.worktree, worktree: wt, file: d.file,
      phase, lastActivity: lastLine(log), logLines: Math.max(0, logLength(d.text)), session, pr, questions: qs,
    })
  }
  return { scannedAt: new Date(now).toISOString(), runs }
}

let cached: RunsView = { scannedAt: "", runs: [] }
let inflight: Promise<void> | null = null
let lastScan = 0
/** Cached view; a rescan starts in the background when older than 30 s. */
export async function getRuns(questions: () => Question[]): Promise<RunsView> {
  if (!inflight && Date.now() - lastScan > 30_000) {
    lastScan = Date.now()
    inflight = buildRuns(questions()).then((v) => { cached = v }).catch(() => {}).finally(() => { inflight = null })
  }
  if (!cached.scannedAt && inflight) await inflight
  const open = questions().filter((q) => q.state === "open")
  return { ...cached, runs: cached.runs.map((r) => ({ ...r, questions: open.filter((q) => q.dossier === r.id).map((q) => q.id) })) }
}
