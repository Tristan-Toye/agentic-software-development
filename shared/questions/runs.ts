// Pipeline discovery: dossiers, stage, session activity, PR/CI. Contract: references/owner-questions.md (Dashboard).
import { activeRepos, repoShown } from "./activerepos.ts"
import { closeSync, existsSync, openSync, readdirSync, readFileSync, readSync, statSync } from "node:fs"
import { homedir } from "node:os"
import { basename, dirname, join, relative } from "node:path"
import type { Question } from "./store.ts"
import { emitChange } from "./events.ts"
import { blockedByOf, controlsFor, type Controls } from "./controls.ts"
import { applyPending } from "./pending.ts"
import { planningRunsAsync } from "./planning.ts"
import { IO_CONCURRENCY, mapLimit, readText } from "./asyncio.ts"
import { offThread } from "./scanpool.ts"
import { readRunner, readRunnerAsync, readUpdates, readUpdatesAsync, readViewCache, runnerActive, runsDir, summaryOf, writeViewCacheAsync, type RunnerState, type Update } from "./runstore.ts"

export const STATUSES = ["planned", "ready", "building", "review", "pr", "done", "dropped"] as const
const TAIL = 256 * 1024
const HEAD = 64 * 1024

export type Front = { pr?: string; id?: string; title?: string; status?: string; branch?: string; jira?: string; updated?: string; created?: string; blocked_by?: string; delivery?: string }
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
  main: string; blockedBy: string[]; controls: Controls; planningRun?: { key: string; state: string } | null; view: "run" | "dossier"
  /** Delivery lane from the dossier front matter (`kanban`); empty for the stub flow. */
  delivery?: string
  deferred: Deferred; deferredOpen: number; runnerLog: string
  /** The key of the run shown for this dossier: `<repo>.<ID>`, or the /plan run of the deferred item it comes from. */
  runKey?: string
  /** Actions pressed whose run has not shown up yet (their buttons are hidden), and the note of one that failed or timed out. */
  pending?: string[]; pendingNotes?: Record<string, string>
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
export type DeferredItem = { id: string; kind: string; title: string; loc: string; risk: string; why: string; state: "open" | "fixing" | "fixed" | "dossier" | "deprecated"; note: string; dossier: string;
  /** The item a dossier is planned from: itself, or another dossier's same issue (`seed: /plan W-128 D-3`, `DEFERRED-SAME`). */
  seed?: { src: string; item: string }; planBranch?: string; ownerKept?: boolean; planning?: "running" | "testing" | "waiting" | "paused" | "done" | "failed" | "stopped"; contract?: string; effort?: string; inReport?: boolean; question?: string; ruling?: string }
export type Deferred = { items: DeferredItem[]; pr: string; report: boolean; branch?: string }

/**
 * The deferred ledger of a build log: `- D-n <kind> <title> — <path:line> — <risk> — <why>[ — contract: <c>][ — effort: <e>]`
 * lines (first one per id wins; the `contract:` and `effort:` parts may sit anywhere after the title), closed by `DEFERRED-FIXED: D-n <sha>` and routed by
 * `DEFERRED-TRIAGE: D-n → dossier — …`; `DEFERRED-FIX: PR <url>` names the stacked PR.
 */
/**
 * The structured record `.discovery/deferred/<ID>.yaml` (JSON in a YAML file, written by `scripts/build_records.py`):
 * `{items: [{id, evidence, fix, boundary, blast_radius, state, title?, kind?, loc?, note?}], sources: []}`. Null when absent or
 * unreadable, which sends the caller back to the build-log parse.
 * The record sits beside the dossier (a worktree's `.discovery/`) or in the main checkout `main`, where `/fix-deferred`
 * writes it; the dossier itself is often read from another worktree or from a branch (a `git:` path), so both places
 * are tried and the newer copy wins.
 */
export function readDeferredRecord(dossierFile: string, id: string, main = ""): DeferredItem[] | null {
  const places = [...(dossierFile.startsWith("git:") ? [] : [join(dirname(dossierFile), "..", "deferred", `${id}.yaml`)]),
    ...(main ? [join(main, ".discovery", "deferred", `${id}.yaml`)] : [])]
  const found = places.flatMap((p) => { try { return [{ p, t: statSync(p).mtimeMs }] } catch { return [] } }).sort((a, b) => b.t - a.t)
  if (!found.length) return null
  let rec: any
  try { rec = JSON.parse(readFileSync(found[0].p, "utf8")) } catch { return null }
  if (!rec || !Array.isArray(rec.items)) return null
  const states: Record<string, DeferredItem["state"]> = { open: "open", fixing: "fixing", fixed: "fixed", dossier: "dossier", wontfix: "deprecated" }
  return rec.items.filter((i: any) => i && /^D-\d+$/.test(String(i.id))).map((i: any): DeferredItem => {
    const note = String(i.note ?? ""), effort = note.match(/\beffort:\s*([SML])\b/i)?.[1]?.toUpperCase()
    const contract = String(i.boundary ?? "").trim().split(/[:\s]/)[0].toLowerCase()
    return {
      id: i.id, kind: String(i.kind ?? "deferred"), title: String(i.title ?? i.evidence ?? ""), loc: String(i.loc ?? ""), risk: String(i.blast_radius ?? ""), why: String(i.fix ?? ""),
      state: states[String(i.state)] ?? "open", note: /^effort:/i.test(note) ? "" : note, dossier: "", ...(contract ? { contract } : {}), ...(effort ? { effort } : {}),
    }
  })
}

export function parseDeferred(log: string, record: DeferredItem[] | null = null): { items: DeferredItem[]; pr: string; branch: string } {
  // A record, when the run wrote one, is the source of the items; the log still supplies the later events and the PR.
  const items = new Map<string, DeferredItem>((record ?? []).map((i) => [i.id, i]))
  let pr = "", branch = "", prFixed = 0
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
    // An item first raised after the ledger (in a review reply, or a question) is triaged without a ledger line of
    // its own: the triage line makes it an item, and the /deferred report fills in its fields when it has them.
    if (tr && !items.has(tr[1])) items.set(tr[1], { id: tr[1], kind: "noticed", title: tr[3].split(/;\s*seed:/)[0].trim() || tr[1], loc: "", risk: "", why: "", state: "open", note: "", dossier: "" })
    if (tr && items.has(tr[1]) && tr[2] === "dossier") {
      const it = items.get(tr[1])!
      if (it.state !== "fixed") { it.state = "dossier"; it.note = tr[3] }
      const sd = tr[3].match(/seed:\s*\/plan\s+([A-Z][A-Z0-9]*-\d+)\s+(D-\d+)\b/)
      if (sd) it.seed = { src: sd[1], item: sd[2] }
      continue
    }
    const same = l.match(/^(?:[-*]\s+)?DEFERRED-SAME:\s*(D-\d+)\s*=\s*([A-Z][A-Z0-9]*-\d+)\s+(D-\d+)\b/)
    if (same && items.has(same[1])) { items.get(same[1])!.seed = { src: same[2], item: same[3] }; continue }
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
      // An item the run already triaged as a direct fix (`fixing`) can still be ruled on: /fix-deferred asks when it
      // cannot prove a fix, and a "leave it open" ruling then drops it (W-162 D-5 read fixing for good).
      if (it && (it.state === "open" || it.state === "fixing")) {
        it.ruling = text
        // "stays open" / "leave it open" / "not now" is a drop (there is no "open past this stub"): the item ends
        // dropped, the ruling its reason. Any other ruling sets the run to work on it.
        if (/\b(stays? open|leave (?:it )?open|left open|keep (?:it )?open|not now|later)\b/i.test(text)) { it.state = "deprecated"; it.note = text }
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
    // The run's own summary of its commits: `DEFERRED-FIX: commits — D-4 817cf9b5, D-1 545dfe3f + b582af0f (…)`.
    const cm = l.match(/^(?:[-*]\s+)?DEFERRED-FIX:\s*commits\b\s*(?:—\s*)?(.*)$/)
    if (cm) {
      for (const part of cm[1].split(/,\s*(?=D-\d+\b)/)) {
        const c = part.match(/^(D-\d+)\s+([0-9a-f]{7,40}(?:\s*\+\s*[0-9a-f]{7,40})*)/)
        const it = c ? items.get(c[1]) : undefined
        if (c && it && (it.state === "open" || it.state === "fixing")) { it.state = "fixed"; it.note = c[2].replace(/\s+/g, "") }
      }
      continue
    }
    const p = l.match(/DEFERRED-FIX:\s*PR\s+(\S+)(?:.*?\bfixed\s+(\d+))?/)
    if (p) { pr = p[1]; if (p[2]) prFixed = Number(p[2]) }
  }
  // The PR line reports how many items the stacked PR fixed. Runs that closed their items there instead of with one
  // `DEFERRED-FIXED:` line each left them `fixing`; when the PR accounts for all of them, they are fixed in that PR.
  const still = [...items.values()].filter((i) => i.state === "fixing")
  const done = [...items.values()].filter((i) => i.state === "fixed").length
  if (pr && still.length && prFixed >= done + still.length) for (const it of still) { it.state = "fixed"; it.note = `in ${pr.replace(/^.*\/pull\//, "PR #")}` }
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
/** `refreshing` is set on a served view while it is the last one read from disk (or a rebuild is running): the page notes it. */
export type RunsView = { scannedAt: string; runs: Run[]; refreshing?: boolean }

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
    if (v === "") {
      // A block list (`pr:` then `  - a` lines) reads as its inline form `[a, b]`, like `pr: [a, b]`.
      const items: string[] = []
      while (i + 1 < lines.length && /^\s+-\s/.test(lines[i + 1])) items.push(lines[++i].replace(/^\s+-\s+/, "").trim().replace(/^(["'])(.*)\1$/, "$2"))
      if (items.length) v = `[${items.join(", ")}]`
    }
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
/**
 * The /work-on phase of a run's updates (newest first; checkpoint names start `Phase <n>`). The phase names are
 * /work-on's, so only a /work-on run's stage lines count: the stages of /fix-deferred, /resolve-merge and other runs
 * are skipped, and a /plan run (a re-plan) makes any older build phase stale. A /work-on run with no stage yet (a
 * restart) shows the phase its earlier /work-on run reached. Returns the newest /work-on stage, `null` when a /plan
 * run came after every /work-on stage (so the caller does not fall back to the build log of that earlier attempt
 * either), and "" when no /work-on stage is recorded at all.
 * Stage lines older than every `start /<command>` line (logs from before runners wrote one) count as /work-on.
 */
export function stagePhase(updates: { kind: string; stage?: string; text: string }[]): string | null {
  let pending = ""   // the newest stage seen since the last start line
  for (const u of updates) {
    if (u.kind === "runner") {
      const cmd = u.text.match(/^start \/([\w-]+)/)?.[1]
      if (!cmd) continue
      if (cmd === "work-on") { if (pending) return pending; continue }
      if (cmd === "plan") return null
      pending = ""     // another command's stages say nothing about the build phase
      continue
    }
    if (u.kind === "stage" && !pending) { const m = `${u.stage ?? ""} ${u.text}`.match(/\b(?:Phase|PHASE)\s+(\d+[a-z]?)\b/); if (m) pending = m[1] }
  }
  return pending
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

// ---- notes laid over a main-checkout dossier ----
// In committed mode a run never writes the main checkout's dossier: a write with no open branch goes to
// `.discovery/notes/<file>` (scripts/dossier_edit.py, git-excluded) and every reader lays it over the dossier, as
// dossier_edit.read_overlaid does: the notes' front matter keys replace the dossier's, and the notes' Build log lines
// follow the dossier's (a line already there is not repeated).
/** The notes file beside a `.discovery/dossiers/<file>`, when one exists. */
export function notesFor(file: string): string | null {
  if (basename(dirname(file)) !== "dossiers") return null
  const n = join(dirname(dirname(file)), "notes", basename(file))
  return existsSync(n) ? n : null
}
const FM = /^---\n([\s\S]*?)\n---\n?/
function fmBlocks(fm: string): Map<string, string[]> {
  const out = new Map<string, string[]>()
  let key = ""
  for (const l of fm.split("\n")) {
    const m = l.match(/^([A-Za-z_][\w-]*):/)
    if (m) { key = m[1]; out.set(key, [l]) } else if (key) out.get(key)!.push(l)
  }
  return out
}
/** TEXT with NOTES laid over it (see above). Text without front matter is returned as it is. */
export function overlayNotes(text: string, notes: string): string {
  const a = text.match(FM), b = notes.match(FM)
  if (!a || !b) return text
  const blocks = fmBlocks(a[1]), extra = fmBlocks(b[1])
  extra.delete("id")
  const lines: string[] = []
  for (const [k, v] of blocks) { lines.push(...(extra.get(k) ?? v)); extra.delete(k) }
  for (const v of extra.values()) lines.push(...v)
  let merged = `---\n${lines.join("\n")}\n---\n` + text.slice(a[0].length)
  const have = new Set(merged.split("\n"))
  const nbody = notes.slice(b[0].length)
  const tail = nbody.includes("## Build log") ? nbody.split("## Build log").slice(1).join("## Build log") : nbody
  const add = tail.split("\n").filter((l) => l.startsWith("- ")).map((l) => l.trimEnd()).filter((l) => !have.has(l))
  if (!add.length) return merged
  const at = merged.search(/^## Build log\s*$/m)
  if (at < 0) return merged.replace(/\n*$/, "\n") + add.join("\n") + "\n"
  const next = merged.slice(at + 1).search(/^## /m)
  const end = next < 0 ? merged.length : at + 1 + next
  const head = merged.slice(0, end).replace(/\n*$/, "\n")
  return head + add.join("\n") + "\n" + (next < 0 ? "" : "\n" + merged.slice(end))
}
/** A dossier file's text with its notes laid over it. */
function readOverlaid(file: string): string {
  const text = readFileSync(file, "utf8")
  const n = notesFor(file)
  if (!n) return text
  try { return overlayNotes(text, readFileSync(n, "utf8")) } catch { return text }
}

/** Dossier files under one root, each parsed. */
export function dossiersIn(root: string): { front: Front; text: string; file: string }[] {
  const dir = join(root, ".discovery", "dossiers")
  let names: string[] = []
  try { names = readdirSync(dir).filter((n) => n.endsWith(".md")).sort() } catch { return [] }
  const out: { front: Front; text: string; file: string }[] = []
  for (const n of names) {
    try {
      const text = readOverlaid(join(dir, n))
      const front = parseFrontMatter(text)
      if (!front.id) front.id = n.replace(/\.md$/, "").replace(/^([A-Za-z]+-\d+).*$/, "$1")
      out.push({ front, text, file: join(dir, n) })
    } catch { /* unreadable: skip */ }
  }
  return out
}

/**
 * Per id, the copy with the longest build log wins (ties: first in sorted worktree order).
 * With `main`, a copy whose file name differs from the main checkout's file for that id is another dossier that once had
 * the same id (a stale worktree from before a renumber) and never wins.
 */
export function pickDossiers(roots: string[], main = ""): { front: Front; text: string; file: string; worktree: string }[] {
  const best = new Map<string, { front: Front; text: string; file: string; worktree: string; len: number }>()
  const mainName = new Map<string, string>()
  if (main) for (const d of dossiersIn(main)) mainName.set(d.front.id!, baseName(d.file))
  for (const wt of [...new Set(roots)].sort()) {
    for (const d of dossiersIn(wt)) {
      const own = mainName.get(d.front.id!)
      if (own && own !== baseName(d.file)) continue
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
  if (!m) return readOverlaid(file)
  const p = Bun.spawnSync(["git", "-C", m[1], "show", `${m[2]}:${m[3]}`], { stdout: "pipe", stderr: "ignore" })
  if (p.exitCode !== 0) throw new Error(`cannot read ${m[3]} on ${m[2]}`)
  return p.stdout.toString()
}
/** `readDossier` without blocking the event loop: a `git:` file's `git show` runs as an async child. */
export async function readDossierAsync(file: string): Promise<string> {
  const m = file.match(GIT_FILE)
  if (!m) {
    const text = (await readText(file)) ?? (() => { throw new Error(`cannot read ${file}`) })()
    const n = notesFor(file)
    const notes = n ? await readText(n) : null
    return notes ? overlayNotes(text, notes) : text
  }
  const r = await shRun(["git", "-C", m[1], "show", `${m[2]}:${m[3]}`], undefined, 30_000)
  if (r.code !== 0) throw new Error(`cannot read ${m[3]} on ${m[2]}`)
  return r.stdout
}
/**
 * Whether a branch name is the build branch of dossier `id`: the id as a whole token, any case, or the dossier's own
 * `branch:` front-matter field (`declared`), with or without `origin/`. Plan and stub branches never are.
 */
export function isBuildBranch(ref: string, id: string, declared?: string): boolean {
  if (NOT_BUILD.test(ref)) return false
  const want = declared?.trim().replace(/^origin\//, "")
  if (want && ref.replace(/^origin\//, "") === want) return true
  return new RegExp(`(?:^|[/_-])${id.replace(/[^\w-]/g, "")}(?:$|[-_/])`, "i").test(ref)
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
      if (!isBuildBranch(ref, d.front.id!, d.front.branch)) continue
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
  for (const [main, wts] of byMain) for (const d of await withBranchCopies(main, pickDossiers(wts, main))) out.push({ ...d, main })
  return out
}

/**
 * The repo a question belongs to: among the runs with its dossier id, the one named by `q.repo` or whose checkouts
 * contain `q.root`. When `q.repo` names a repo the runs know, it is the answer even with no run of that dossier yet:
 * dossier ids repeat across repos, and a question asked moments after its dossier was written must not be filed under
 * another repo's dossier of the same id. The only candidate is the answer only when `q.repo` is not a known repo.
 */
export function questionRepo(q: { repo: string; dossier: string; root?: string }, runs: { repo: string; id: string; main: string; worktree: string }[], byId?: Map<string, { repo: string; id: string; main: string; worktree: string }[]>, repos?: Set<string>): string | undefined {
  // `byId` is `runs` grouped by dossier id (runsById): linkQuestions asks for every question, and a filter over all runs each time cost O(questions x runs) on the main thread.
  const c = byId ? byId.get(q.dossier) ?? [] : runs.filter((r) => r.id === q.dossier)
  const inside = (p: string, d: string) => !!p && !!d && (p === d || p.startsWith(d + "/"))
  const hit = c.find((r) => r.repo === q.repo) ?? c.find((r) => inside(q.root ?? "", r.worktree) || inside(q.root ?? "", r.main))
  if (hit) return hit.repo
  if (q.repo && (repos ? repos.has(q.repo) : runs.some((r) => r.repo === q.repo))) return q.repo
  return c.length === 1 ? c[0].repo : undefined
}

/** `runs` grouped by dossier id, each group in the order of `runs`. */
export function runsById<T extends { id: string }>(runs: T[]): Map<string, T[]> {
  const m = new Map<string, T[]>()
  for (const r of runs) { const l = m.get(r.id); if (l) l.push(r); else m.set(r.id, [r]) }
  return m
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
  return (await shOrNull(cmd, cwd, timeoutMs)) ?? ""
}
/** An async child with its exit code and both streams (a timeout kills it: code 124). The handlers' replacement for Bun.spawnSync, which freezes every other request while it runs. */
export async function shRun(cmd: string[], cwd: string | undefined, timeoutMs: number): Promise<{ code: number; stdout: string; stderr: string }> {
  try {
    const p = Bun.spawn(cmd, { cwd, stdout: "pipe", stderr: "pipe", stdin: "ignore" })
    let timedOut = false
    const t = setTimeout(() => { timedOut = true; p.kill() }, timeoutMs)
    const [stdout, stderr, code] = await Promise.all([new Response(p.stdout).text(), new Response(p.stderr).text(), p.exited])
    clearTimeout(t)
    return { code: timedOut ? 124 : code, stdout, stderr }
  } catch (e) { return { code: 127, stdout: "", stderr: String((e as Error)?.message ?? e) } }
}
/** `sh`, but a failure (non-zero exit, timeout, spawn error) is null instead of "", for a caller that must not read
 * a failed command as an empty answer. */
export async function shOrNull(cmd: string[], cwd: string | undefined, timeoutMs: number): Promise<string | null> {
  try {
    const p = Bun.spawn(cmd, { cwd, stdout: "pipe", stderr: "ignore", stdin: "ignore" })
    const t = setTimeout(() => p.kill(), timeoutMs)
    const out = await new Response(p.stdout).text()
    clearTimeout(t)
    return (await p.exited) === 0 ? out : null
  } catch { return null }
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
  const active = activeRepos()   // the active-repos file, when there is one, lists the only repos shown (and ticked by auto work)
  const out: string[] = []
  for (const w of all) {
    if (!existsSync(join(w, ".discovery", "dossiers"))) continue
    if (isStackCheckout(w)) continue   // the /stub stack checkout mirrors a stub head, never a dossier source
    const main = await mainOf(w)
    if (active === null || repoShown(baseName(main), active)) out.push(w)
  }
  return out
}

/**
 * The stack checkout (scripts/stack_checkout.py): `<main>-stack`, a clone beside the main checkout `<main>`, detached
 * at a stub head for the full stack run. `exists` is injectable for tests.
 */
export function isStackCheckout(dir: string, exists: (p: string) => boolean = existsSync): boolean {
  const d = dir.replace(/\/+$/, "")
  if (!d.endsWith("-stack")) return false
  const main = d.slice(0, -"-stack".length)
  return main.length > 0 && !main.endsWith("/") && exists(join(main, ".git")) && exists(join(d, ".git"))
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
    const l = m.get(q.dossier)
    if (l) l.push(qaOf(q)); else m.set(q.dossier, [qaOf(q)])
  }
  return m
}
const qaOf = (q: Question): QA => ({ id: q.id, text: q.text, answer: q.answer?.text ?? "", option: q.answer?.option as string ?? "", asked: q.ts, answeredAt: q.answer?.ts ?? "" })

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
  const sources = new Map<string, string>()   // "<repo>/<src ID>/<D-n>" -> the dossier /plan made from it
  for (const d of await pickDossiersByRepo(roots)) {
    const f = d.front
    const log = buildLog(d.text)
    const status = f.status ?? "planned"
    // The latest /work-on run's own stage lines (newest first) outrank the build log's text.
    // Every update line, not the 50 the view shows: the newest run's start line can be further back than that.
    const sp = stagePhase(readUpdates(`${baseName(d.main)}.${f.id}`, runsDir(), Infinity))
    const phaseHit = sp === null ? "" : sp || lastPhase(log)
    const phase = phaseHit || (status === "planned" || status === "ready" ? "plan" : "")
    const wt = d.worktree
    const repoName = baseName(await mainOf(wt))
    const { key: runKey, runner } = runnerOf(repoName, f.id!, log)
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
      running: isRunning(status) || runnerActive(runner), summary: summaryOf(d.text), updates: readUpdates(runKey), runner, runKey,
      main, delivery: f.delivery === "kanban" ? "kanban" : "", blockedBy: blockedByOf(f.blocked_by), controls: controlsFor({ status, blockedBy: blockedByOf(f.blocked_by), runner }, () => undefined), view: "dossier",
      runnerLog: runner ? join(runsDir(), `${runKey}.runner.log`) : "",
      ...(() => {
        for (const x of deferredSources(log)) sources.set(`${repoName}/${x.src}/${x.item}`, f.id!)
        const pd = deferredSince(f.updated ?? "", runner) ? parseDeferred(log, readDeferredRecord(d.file, f.id!, d.main)) : { items: [], pr: "", branch: "" }
        let report = ""
        try { report = readFileSync(join(runsDir(), `${repoName}.${f.id}.deferred.md`), "utf8") } catch { /* none */ }
        const deferred: Deferred = { items: withReport(pd.items, report), pr: pd.pr, report: report !== "", branch: pd.branch }
        return { deferred, deferredOpen: pd.items.filter((i) => i.state === "open").length }
      })(),
    })
  }
  // Dossiers planned but not merged yet live only on their plan/* branch (the scan leaves plan branches out): read
  // their DEFERRED-SOURCE lines there, so an item planned in an open plan PR links to it.
  const planOn = new Map<string, string>()   // "<repo>/<src>/<D-n>" -> the plan branch
  for (const main of new Set(runs.map((r) => r.main))) {
    const repoName = baseName(main)
    for (const x of await planBranchSources(main)) {
      const k = `${repoName}/${x.src}/${x.item}`
      if (!sources.has(k)) { sources.set(k, x.id); planOn.set(k, x.branch) }
    }
  }
  linkPlannedItems(runs, sources, undefined, planOn)
  return { scannedAt: new Date(now).toISOString(), runs }
}
/**
 * An item routed to its own dossier links to it from either side: the source's `DEFERRED-DOSSIER: D-n → <ID>`, or
 * the new dossier's own `DEFERRED-SOURCE: <src ID> D-n` (written by /plan, in the dossier it owns, so no other
 * run's copy is touched). An item whose /plan run (`<repo>.<ID>.plan-<D>`) is under way carries its state.
 */
/**
 * The run of a dossier and its key: its own `<repo>.<ID>` run, else, for a dossier /plan is writing from a deferred
 * item, that planning run (`<repo>.<src>.plan-<D-n>`, named by the dossier's `DEFERRED-SOURCE` line). So a dossier
 * being planned is live like one being built: Running now, the stub card, its timeline.
 */
export function runnerOf(repo: string, id: string, log: string, read: (key: string) => RunnerState | null = (k) => readRunner(k)): { key: string; runner: RunnerState | null } {
  const ownKey = `${repo}.${id}`
  const own = read(ownKey)
  if (own) return { key: ownKey, runner: own }
  for (const x of deferredSources(log)) {
    const k = `${repo}.${x.src}.plan-${x.item}`
    const r = read(k)
    if (r) return { key: k, runner: r }
  }
  return { key: ownKey, runner: null }
}
export function deferredSources(log: string): { src: string; item: string }[] {
  return [...log.matchAll(/^(?:[-*]\s+)?DEFERRED-SOURCE:\s*([A-Z][A-Z0-9]*-\d+)\s+(D-\d+)\b/gm)].map((m) => ({ src: m[1], item: m[2] }))
}
const planCache = new Map<string, { sig: string; v: { src: string; item: string; id: string; branch: string }[] }>()
/** DEFERRED-SOURCE lines of the dossiers each open plan/* branch adds over development (cached by the branch tips). */
export async function planBranchSources(main: string): Promise<{ src: string; item: string; id: string; branch: string }[]> {
  const refs = (await sh(["git", "-C", main, "for-each-ref", "--format=%(objectname) %(refname:short)", "refs/remotes/origin/plan/"], undefined, 5000)).trim()
  const c = planCache.get(main)
  if (c && c.sig === refs) return c.v
  const out: { src: string; item: string; id: string; branch: string }[] = []
  for (const line of refs ? refs.split("\n") : []) {
    const ref = line.split(" ")[1]
    if (!ref) continue
    const files = (await sh(["git", "-C", main, "diff", "--name-only", `origin/development...${ref}`, "--", ".discovery/dossiers"], undefined, 10000)).split("\n").filter((f) => /\.md$/.test(f))
    for (const f of files) {
      const id = f.match(/([A-Z][A-Z0-9]*-\d+)-[^/]*\.md$/)?.[1]
      if (!id) continue
      const text = await sh(["git", "-C", main, "show", `${ref}:${f}`], undefined, 10000)
      for (const x of deferredSources(text)) out.push({ ...x, id, branch: ref.replace(/^origin\//, "") })
    }
  }
  planCache.set(main, { sig: refs, v: out })
  return out
}
export function linkPlannedItems(runs: Run[], sources: Map<string, string>, planState: (key: string) => RunnerState["state"] | undefined = (k) => readRunner(k)?.state, planOn: Map<string, string> = new Map()): void {
  const src = sources
  for (const r of runs) for (const i of r.deferred?.items ?? []) {
    if (i.state !== "dossier") continue
    // The same issue as another dossier's item is planned once, from there: follow its seed.
    const from = i.seed && i.seed.src !== r.id ? i.seed : { src: r.id, item: i.id }
    if (!i.dossier) {
      const k = `${r.repo}/${from.src}/${from.item}`
      i.dossier = src.get(k) ?? ""
      if (i.dossier && planOn.has(k)) i.planBranch = planOn.get(k)
    }
    const st = planState(`${r.repo}.${from.src}.plan-${from.item}`)
    if (st) i.planning = st
  }
}
/** Questions linked to runs of their own repo only; `open` ids and `answered` Q&A per run. */
export function linkQuestions(runs: Run[], questions: Question[]): Map<string, { open: string[]; answered: QA[] }> {
  const out = new Map<string, { open: string[]; answered: QA[] }>()
  const key = (repo: string, id: string) => `${repo}/${id}`
  for (const r of runs) out.set(key(r.repo, r.id), { open: [], answered: [] })
  const byId = runsById(runs)
  const repos = new Set(runs.map((r) => r.repo))
  for (const q of questions) {
    const repo = questionRepo(q, runs, byId, repos)
    const slot = repo === undefined ? undefined : out.get(key(repo, q.dossier))
    if (!slot) continue
    if (q.state === "open") slot.open.push(q.id)
    else if (q.state === "answered") slot.answered.push(qaOf(q))
  }
  return out
}

// A restart serves the last view from disk at once; the first scan replaces it (readViewCache).
let cached: RunsView = readViewCache<RunsView>("runs") ?? { scannedAt: "", runs: [] }
let inflight: Promise<void> | null = null
let lastScan = 0
let lastSig = ""
/**
 * The deferred items that need the owner, the count a dossier's row shows. With no fix run live, every open item not
 * dropped by a ruling. While `/fix-deferred` is live the open items are in the agent's hands, so only those whose
 * question still waits on the owner count.
 */
export function undecidedDeferred(items: DeferredItem[], fixLive: boolean, openQuestions: Set<string>): number {
  return items.filter((i) => i.state === "open" && (!fixLive || (!!i.question && openQuestions.has(i.question)))).length
}
/** Updates, runner state and the owner's rulings are fresh on every request; read async so a slow disk never stalls the server. */
async function liveStore(r: Run): Promise<Pick<Run, "updates" | "runner" | "running" | "deferred" | "deferredOpen">> {
  const key = r.runKey || `${r.repo}.${r.id}`
  const [runner, rulings, updates] = await Promise.all([readRunnerAsync(key), readText(ownerRulingsFile(`${r.repo}.${r.id}`)), readUpdatesAsync(key)])
  const deferred = applyOwnerRulings(r.deferred, rulings)
  return { updates, runner, running: isRunning(r.status) || runnerActive(runner), deferred,
    deferredOpen: deferred.items.filter((i) => i.state === "open").length }
}
/**
 * The owner's own rulings on deferred items, made on the dashboard: `<key>.deferred-owner.json`
 * `{ "D-2": { "drop": true, "ts": "…" } }`. Kept beside the run, not in the dossier, which a live run may be
 * editing. "Drop" ends an open item dropped (state `deprecated`, the ruling its reason: not counted, not swept in
 * by Fix all); "Reopen" (`drop: false`) undoes it. The older `keepOpen: true` ("Leave open") reads as a drop: the
 * owner's rule is that leaving an item open is dropping it, so rulings already on disk need no migration.
 */
export function ownerRulingsFile(key: string, dir = runsDir()): string { return join(dir, `${key}.deferred-owner.json`) }
export function withOwnerRulings(d: Deferred, key: string, dir = runsDir()): Deferred {
  let text: string | null = null
  try { text = readFileSync(ownerRulingsFile(key, dir), "utf8") } catch { /* none */ }
  return applyOwnerRulings(d, text)
}
/** The rulings file's text (null when there is none) applied to the deferred items. */
export function applyOwnerRulings(d: Deferred, text: string | null): Deferred {
  if (text === null) return d
  let o: Record<string, { drop?: boolean; keepOpen?: boolean; ts?: string }> = {}
  try { o = JSON.parse(text) } catch { return d }
  return { ...d, items: d.items.map((i) => {
    const r = o[i.id]
    if (!r || i.state !== "open") return i
    if (r.drop === true || (r.drop === undefined && r.keepOpen === true)) {
      const why = i.ruling || "left open on the dashboard: leaving it open is dropping it"
      return { ...i, state: "deprecated", ruling: why, note: why, ownerKept: true }
    }
    return i
  }) }
}
/** Whether a scan has finished in this process: until then the view is the one read from disk at start. */
let scannedLive = false
function startScan(questions: () => Question[], build: (q: Question[]) => Promise<RunsView>): void {
  lastScan = Date.now()
  inflight = offThread<RunsView>("runs", questions(), () => build(questions())).then((v) => {
    const sig = JSON.stringify(v.runs.map((r) => [r.repo, r.id, r.status, r.phase, r.lastActivity, r.session.state, r.pr, r.session.activity?.doing ?? "", r.session.activity?.background.length ?? 0]))
    cached = v
    scannedLive = true
    void writeViewCacheAsync("runs", v)
    if (sig !== lastSig) { lastSig = sig; emitChange("runs") }
    emitChange("scan")
  }).catch(() => { lastScan = 0 }).finally(() => { inflight = null })
}
/**
 * Cached view; a rescan starts in the background when older than 30 s, and every caller shares the one in flight.
 * A restart serves the disk copy at once with `refreshing: true`. `fresh` is for a caller that acts on the view (run
 * control, merge, routing): it waits for the first scan of this process, so it never acts on the disk copy.
 */
export async function getRuns(questions: () => Question[], opts: { fresh?: boolean; build?: (q: Question[]) => Promise<RunsView>; live?: typeof liveStore } = {}): Promise<RunsView> {
  const build = opts.build ?? buildRuns   // `build` and `live` are test seams
  if (!inflight && Date.now() - lastScan > 30_000) startScan(questions, build)
  if (inflight && (!cached.scannedAt || (opts.fresh && !scannedLive))) await inflight
  if (opts.fresh && !scannedLive) { if (!inflight) startScan(questions, build); await inflight }
  const linked = linkQuestions(cached.runs, questions())
  const lives = await mapLimit(cached.runs, IO_CONCURRENCY, (r) => (opts.live ?? liveStore)(r).catch(() => undefined))
  const live = cached.runs.map((r, i) => {
    // A run whose live store could not be read keeps its scanned state: one bad file never takes the whole view down.
    const l = lives[i] ?? { updates: r.updates ?? [], runner: r.runner, running: isRunning(r.status), deferred: r.deferred, deferredOpen: r.deferredOpen }
    const qa = linked.get(`${r.repo}/${r.id}`)
    return { ...r, ...l, questions: qa?.open ?? [], answered: qa?.answered ?? [], view: viewFor(r.status, l.running, l.updates.length) }
  })
  const statusBy = new Map<string, string>()   // first run of a (repo, id), as `find` gave it; a map, since controlsFor asks per blocker of every run
  for (const x of live) { const k = `${x.repo}/${x.id}`; if (!statusBy.has(k)) statusBy.set(k, x.status) }
  const statusOf = (repo: string) => (id: string) => statusBy.get(`${repo}/${id}`)
  // A pressed run button stays hidden while its run starts (pending.ts): the server-side marker outlives re-renders and tabs.
  const now = Date.now()
  // A dossier a live seed / plan-from-deferred run claimed is being planned there: no Start, the card names that run.
  const planByRepo = new Map<string, Awaited<ReturnType<typeof planningRunsAsync>>>(await Promise.all([...new Set(live.map((r) => r.repo))].map(async (repo) => [repo, await planningRunsAsync(repo)] as const)))
  const planningOf = (r: { repo: string; id: string; runKey: string }) => {
    const p = planByRepo.get(r.repo)![r.id.toUpperCase()]
    return p && p.key !== r.runKey ? p : null
  }
  return { ...cached, refreshing: !scannedLive || inflight !== null, runs: live.map((r) => { const planningRun = planningOf(r); return applyPending({ ...r, planningRun, controls: controlsFor({ ...r, planning: planningRun }, statusOf(r.repo)) }, now) }) }
}
