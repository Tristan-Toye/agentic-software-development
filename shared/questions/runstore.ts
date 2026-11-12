// Run updates store, runner state, dossier summary and a tiny markdown renderer. Contract: references/runner.md, references/owner-questions.md (Dashboard).
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import { rulingRefused } from "./rulings.ts"

export type Update = { ts: string; kind: "runner" | "stage" | "update"; text: string; stage?: string; state?: string; auto?: boolean; event?: string; status?: string; agent_id?: string }
export type RunnerState = { state: "running" | "testing" | "waiting" | "paused" | "queued" | "done" | "failed" | "stopped"; waitingFor?: "stub"; stage: string; pid: number; started: string; root: string; account?: string; resumesAt?: string; command?: string; session?: string; items?: string }

export const runsDir = (): string => process.env.ASD_RUNS_DIR || join(homedir(), ".agentic", "runs")

export type IntegrateState = { state: string; pid?: number; error?: string; note?: string; finished?: string[] }

/** The stub's integration file, if it was ever integrated. A merging integration whose process is gone has failed. */
export function readIntegrate(key: string, dir = runsDir(), alive: (pid: number) => boolean = pidAlive): IntegrateState | null {
  try {
    const j = JSON.parse(readFileSync(join(dir, `${key}.integrate.json`), "utf8"))
    if (j?.state === "merging" && j.pid && !alive(Number(j.pid))) return { ...j, state: "failed", error: "the integration stopped before the merges were done; press Integrate again" }
    return j
  } catch { return null }
}

/** Integrate was just pressed: reads as merging at once, before the process it started has written anything. What an earlier start picked stays. */
export function markIntegrating(key: string, fields: { pid: number; root: string; branch: string }, dir = runsDir()): void {
  const path = join(dir, `${key}.integrate.json`)
  let cur: Record<string, unknown> = {}
  try { cur = JSON.parse(readFileSync(path, "utf8")) } catch { /* the first integration */ }
  const tmp = `${path}.${process.pid}.tmp`
  writeFileSync(tmp, JSON.stringify({ ...cur, ...fields, state: "merging", error: "", ts: new Date().toISOString().replace(/\.\d+Z$/, "Z") }))
  renameSync(tmp, path)
}

/** The stub's PR is open, but nothing is working on it: its merges failed, or its /stub run ended without finishing. */
export function integrationStopped(integrate: { state: string } | null, runner: { state: string } | null): boolean {
  return integrate?.state === "failed" || (integrate?.state === "stub" && !!runner && ["stopped", "failed"].includes(runner.state))
}

/** What a stub whose PR is open is actually doing, in words for its card and page. */
export function integrationPhase(integrate: { state: string } | null, runner: { state: string } | null): string {
  if (integrationStopped(integrate, runner)) return "integration stopped"
  if (integrate?.state === "merging") return "merging dossiers into stub"
  if (runner && ["running", "testing", "waiting", "paused", "queued"].includes(runner.state)) return "validating stub"
  if (integrate?.state === "merged") return "merged into development"
  if (runner?.state === "done") return "validation finished · your merge"
  return "integrating"
}

/** Whether Integrate is offered, on the stub's card and on its page: an open stub, or one whose integration failed and can be retried. Never while its merges or its /stub run are going. */
export function canIntegrate(stubState: string, integrate: { state: string } | null, runner: { state: string } | null): boolean {
  if (runner && ["running", "testing", "waiting", "paused", "queued"].includes(runner.state)) return false
  return stubState === "accepting" ? integrate?.state !== "merging" : stubState === "integrating" && integrate?.state === "failed"
}

/** Updates of `<repo>.<ID>`, newest first, at most `limit`; torn or foreign lines are skipped. */
export function readUpdates(key: string, dir = runsDir(), limit = 50): Update[] {
  let text = ""
  try { text = readFileSync(join(dir, `${key}.jsonl`), "utf8") } catch { return [] }
  const out: Update[] = []
  for (const l of text.split("\n")) {
    if (!l.trim()) continue
    try {
      const o = JSON.parse(l)
      if (typeof o?.text !== "string" || !["runner", "stage", "update"].includes(o.kind)) continue
      out.push({ ts: String(o.ts ?? ""), kind: o.kind, text: o.text, stage: typeof o.stage === "string" ? o.stage : undefined, state: typeof o.state === "string" ? o.state : undefined, ...(o.auto === true ? { auto: true } : {}), ...(typeof o.event === "string" ? { event: o.event } : {}), ...(typeof o.status === "string" ? { status: o.status } : {}), ...(typeof o.agent_id === "string" && o.agent_id ? { agent_id: o.agent_id } : {}) })
    } catch { /* torn line */ }
  }
  return out.reverse().slice(0, limit)
}

export function pidAlive(pid: number): boolean {
  if (!pid || pid < 1) return false
  try { process.kill(pid, 0); return true } catch (e) { return (e as NodeJS.ErrnoException).code === "EPERM" }
}

/** `<key>.runner.json`; a running or waiting state with a dead pid reads as stopped. null when absent. */
export function readRunner(key: string, dir = runsDir(), alive: (pid: number) => boolean = pidAlive): RunnerState | null {
  try {
    const j = JSON.parse(readFileSync(join(dir, `${key}.runner.json`), "utf8"))
    let state = ["running", "testing", "waiting", "paused", "queued", "done", "failed", "stopped"].includes(j.state) ? j.state : "stopped"
    if ((state === "running" || state === "testing" || state === "waiting" || state === "paused" || state === "queued") && !alive(Number(j.pid))) state = "stopped"
    // A run that waits with no question is waiting for the stub being integrated (`wait_for_stub`), not for the owner.
    const waitingFor = state === "waiting" && Array.isArray(j.questions) && j.questions.length === 0 ? { waitingFor: "stub" as const } : {}
    return { ...waitingFor, state, stage: String(j.stage ?? ""), pid: Number(j.pid) || 0, started: String(j.started ?? ""), root: typeof j.root === "string" ? j.root : "", ...(typeof j.account === "string" ? { account: j.account } : {}), ...(typeof j.resumes_at === "string" ? { resumesAt: j.resumes_at } : {}), ...(typeof j.command === "string" ? { command: j.command } : {}), ...(typeof j.session === "string" && j.session ? { session: j.session } : {}), ...(typeof j.items === "string" && j.items ? { items: j.items } : {}) }
  } catch { return null }
}
/** A finished run's last reply and the owner's chat with its session (`run_dossier.py chat`). */
export type ChatLine = { ts: string; from: "owner" | "agent" | "runner"; text: string }
export type Conversation = { result: { ts: string; text: string; command: string } | null; chat: ChatLine[]; busy: boolean }
/**
 * The run's last reply: its newest `result` line, else (runs from before the runner recorded one) the newest note the
 * agent posted before the runner's last `stage N end`. Only for a finished run (`done`). The chat lines that follow it,
 * and whether the agent is still answering (`<key>.chat.json` thinking with a live pid).
 */
export function readConversation(key: string, done: boolean, dir = runsDir(), alive: (pid: number) => boolean = pidAlive): Conversation {
  let lines: any[] = []
  try { lines = readFileSync(join(dir, `${key}.jsonl`), "utf8").split("\n").filter((l) => l.trim()).map((l) => { try { return JSON.parse(l) } catch { return null } }).filter(Boolean) } catch { /* none */ }
  let result: Conversation["result"] = null
  for (let i = lines.length - 1; i >= 0 && !result; i--) if (lines[i].kind === "result" && typeof lines[i].text === "string") result = { ts: String(lines[i].ts ?? ""), text: lines[i].text, command: String(lines[i].command ?? "") }
  if (!result && done) {
    let end = -1
    for (let i = lines.length - 1; i >= 0; i--) if (lines[i].kind === "runner" && /^stage \S+ end\b/.test(String(lines[i].text))) { end = i; break }
    for (let i = end - 1; i >= 0; i--) if (lines[i].kind === "update" && lines[i].auto !== true && lines[i].event !== "owner-message" && typeof lines[i].text === "string" && lines[i].text.trim()) { result = { ts: String(lines[i].ts ?? ""), text: lines[i].text, command: "" }; break }
  }
  const since = result?.ts ?? ""
  const chat: ChatLine[] = lines.filter((o) => o.kind === "chat" && typeof o.text === "string" && ["owner", "agent", "runner"].includes(o.from) && String(o.ts ?? "") >= since)
    .map((o) => ({ ts: String(o.ts ?? ""), from: o.from, text: o.text }))
  let busy = false
  try {
    const c = JSON.parse(readFileSync(join(dir, `${key}.chat.json`), "utf8"))
    busy = c.state === "thinking" && (c.pid ? alive(Number(c.pid)) : Date.now() - Date.parse(String(c.since ?? "")) < 120_000)
  } catch { /* no chat yet */ }
  return { result, chat, busy }
}
/** A tool call auto mode refused (the PermissionDenied hook), not ruled on yet. */
export type Block = { id: string; ts: string; tool: string; target: string; reason: string; agent: string }
/** The refused calls of a run that the owner has not ruled on (`block-ruling` lines), newest first, at most `limit`; one per target. */
/** A runner line that starts a new stage process: a block an older process hit has no one left waiting on it. */
const NEW_PROCESS = /^(stage \d+ start|start \/|restarting stage|resumed after)/
export function readBlocks(key: string, dir = runsDir(), limit = 5): Block[] {
  let all: any[] = []
  try { all = readFileSync(join(dir, `${key}.jsonl`), "utf8").split("\n").filter((l) => l.includes('"event":"block') || (l.includes('"runner"') && /stage \d+ start|start \/|restarting stage|resumed after/.test(l))).map((l) => { try { return JSON.parse(l) } catch { return null } }).filter(Boolean) } catch { return [] }
  // Only the current stage process's blocks are open: the agent that hit an older one is gone, and the stage that
  // replaced it either did without the call or will hit (and record) it again.
  let from = 0
  all.forEach((o, i) => { if (o.kind === "runner" && NEW_PROCESS.test(String(o.text ?? ""))) from = i + 1 })
  const lines = all.slice(from).filter((o) => o.event === "blocked" || o.event === "block-ruling")
  const ruled = new Set(lines.filter((o) => o.event === "block-ruling").map((o) => String(o.tool_use_id ?? "")))
  const ruledTargets = new Set(lines.filter((o) => o.event === "block-ruling" && o.scope === "always").map((o) => String(o.target ?? "")))
  const seen = new Set<string>(); const out: Block[] = []
  for (const o of lines.reverse()) {
    if (o.event !== "blocked" || ruled.has(String(o.tool_use_id ?? "")) || ruledTargets.has(String(o.target ?? ""))) continue
    const t = String(o.target ?? "")
    if (seen.has(t)) continue
    seen.add(t)
    out.push({ id: String(o.tool_use_id ?? ""), ts: String(o.ts ?? ""), tool: String(o.tool ?? ""), target: t, reason: String(o.reason ?? ""), agent: String(o.agent_id ?? "") })
    if (out.length >= limit) break
  }
  return out
}
/**
 * The permission rule "Always allow" saves: `Bash(<command>)` for a shell command (the owner may widen it to a prefix,
 * `Bash(ssh vm-test *)`), `<Tool>(<path>)` otherwise. Refuses rules auto mode drops or that allow anything at all.
 */
export function checkRule(rule: string): string {
  const m = rule.trim().match(/^([A-Z][A-Za-z]+)\((.+)\)$/s)
  if (!m) return "a rule looks like Bash(ssh vm-test *)"
  const inner = m[2].trim()
  if (!inner || /^\*+$/.test(inner) || /^\*\s/.test(inner)) return "that rule would allow every command"
  if (m[1] === "Bash" && /^(bash|sh|zsh|python3?|node|bun|npx|npm|uv|pip3?|perl|ruby)\b(\s+\*|\*|$)/.test(inner)) return "a rule for a whole interpreter is dropped in auto mode; name the command"
  return rulingRefused(rule)
}
export const runnerActive = (r: RunnerState | null): boolean => r?.state === "running" || r?.state === "testing" || r?.state === "waiting" || r?.state === "paused" || r?.state === "queued"

/** First paragraph of the `## Problem` section, else of the body after the front matter and title; <= 300 chars. */
export function summaryOf(text: string): string {
  let body = text
  if (body.startsWith("---")) { const i = body.indexOf("\n---", 3); body = i < 0 ? body : body.slice(i + 4) }
  const para = (s: string): string => {
    let cur: string[] = []
    let fence = false
    for (const raw of s.split("\n")) {
      const l = raw.trim()
      if (l.startsWith("```")) { fence = !fence; continue }
      if (fence) continue
      // Evidence lines (FACT:, INFERENCE:, UNKNOWN:, anchors) are not a summary.
      if (!cur.length && /^(?:[-*]\s+)?(?:FACT|INFERENCE|UNKNOWN|UNMEASURED|ASSUMPTION)\b/.test(l)) continue
      if (!l || /^#{1,6}\s/.test(l) || l.startsWith("<!--")) { if (cur.length) break; continue }
      cur.push(l.replace(/^[-*]\s+/, ""))
    }
    return cur.join(" ")
  }
  const pm = body.match(/^##\s+Problem\b.*$/m)
  let p = pm ? para(body.slice(pm.index! + pm[0].length)) : ""
  if (!p) p = para(body.slice(0))
  p = p.replace(/\s+/g, " ")
  return p.length > 300 ? p.slice(0, 299) + "…" : p
}

const esc = (s: string): string => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;")
/** Inline markdown on escaped text: `code`, **bold**, and [text](http(s) url) links (other schemes stay text). */
const inline = (s: string): string => {
  const codes: string[] = []
  return esc(s)
    .replace(/`([^`]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`)
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
    .replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${codes[+i]}</code>`)
}

const LIST_RE = /^(\s*)([-*]|\d+\.)\s+/
const cells = (row: string): string[] => row.trim().replace(/^\|/, "").replace(/\|$/, "").split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, "|"))

/** A pipe table: a header row, a `---` separator row, then body rows. Without the separator it is not a table. */
function table(rows: string[]): string {
  if (rows.length < 2 || !/^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(rows[1])) return `<pre>${esc(rows.join("\n"))}</pre>`
  const head = cells(rows[0]).map((c) => `<th>${inline(c)}</th>`).join("")
  const body = rows.slice(2).map((r) => `<tr>${cells(r).map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`
}

/** Minimal read-only markdown: headings, paragraphs, nested lists, code fences, pipe tables, inline code, bold and
 *  http(s) links. Everything escaped. */
export function renderMarkdown(md: string): string {
  const out: string[] = []
  const lines = md.split("\n")
  let i = 0
  while (i < lines.length) {
    const l = lines[i]
    if (l.trim().startsWith("```")) {
      const code: string[] = []
      for (i++; i < lines.length && !lines[i].trim().startsWith("```"); i++) code.push(lines[i])
      i++
      out.push(`<pre>${esc(code.join("\n"))}</pre>`)
    } else if (/^\s*\|/.test(l)) {
      const t: string[] = []
      for (; i < lines.length && /^\s*\|/.test(lines[i]); i++) t.push(lines[i])
      out.push(table(t))
    } else if (/^#{1,6}\s/.test(l)) {
      const n = l.match(/^#+/)![0].length
      out.push(`<h${n}>${inline(l.replace(/^#+\s*/, ""))}</h${n}>`); i++
    } else if (LIST_RE.test(l)) {
      // Items with their indent; a deeper indent opens a nested list inside the previous item.
      const items: { ind: number; ord: boolean; text: string }[] = []
      for (; i < lines.length && LIST_RE.test(lines[i]); i++) {
        const m = lines[i].match(LIST_RE)!
        let text = lines[i].slice(m[0].length)
        while (i + 1 < lines.length && /^\s{2,}\S/.test(lines[i + 1]) && !LIST_RE.test(lines[i + 1])) text += " " + lines[++i].trim()
        items.push({ ind: m[1].replace(/\t/g, "    ").length, ord: /\d/.test(m[2]), text })
      }
      let k = 0
      const level = (ind: number): string => {
        const ord = items[k].ord
        const lis: string[] = []
        while (k < items.length && items[k].ind >= ind && !(items[k].ind === ind && items[k].ord !== ord)) {
          if (items[k].ind > ind && lis.length) { lis[lis.length - 1] = lis[lis.length - 1].replace(/<\/li>$/, level(items[k].ind) + "</li>"); continue }
          lis.push(`<li>${inline(items[k++].text)}</li>`)
        }
        const tag = ord ? "ol" : "ul"
        return `<${tag}>${lis.join("")}</${tag}>`
      }
      while (k < items.length) out.push(level(items[k].ind))
    } else if (!l.trim()) i++
    else {
      const p: string[] = []
      for (; i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*([-*]|\d+\.)\s|\s*\||\s*```)/.test(lines[i]); i++) p.push(lines[i].trim())
      out.push(`<p>${inline(p.join(" "))}</p>`)
    }
  }
  return out.join("\n")
}

export function dossierPage(id: string, repo: string, md: string): string {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${esc(id)} (${esc(repo)})</title>
<style>:root{color-scheme:light dark;--bg:#fff;--fg:#1b1d21;--bd:#dfe2e7;--chip:#eceef2}@media (prefers-color-scheme:dark){:root{--bg:#111316;--fg:#e6e8eb;--bd:#2c3037;--chip:#272b32}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}main{max-width:860px;margin:0 auto;padding:16px}
pre{background:var(--chip);padding:10px;border-radius:6px;overflow-x:auto;font-size:13px}code{background:var(--chip);padding:0 4px;border-radius:4px}h1,h2,h3{border-bottom:1px solid var(--bd);padding-bottom:3px}a{color:inherit}</style></head><body><main><p><a href="/">&larr; dashboard</a> &middot; ${esc(repo)} &middot; read-only</p>${renderMarkdown(md)}</main></body></html>`
}

/**
 * The last scanned dashboard view on disk, so a restarted server answers at once from it while its first scan (git and
 * gh per repo, tens of seconds) runs in the background. `ASD_VIEW_CACHE_DIR` sets the folder; under a test's own
 * `ASD_RUNS_DIR` without it there is no cache. Outside the runs dir, so writing it never wakes the runs watcher.
 */
export function viewCacheDir(): string | null {
  if (process.env.ASD_VIEW_CACHE_DIR) return process.env.ASD_VIEW_CACHE_DIR
  return process.env.ASD_RUNS_DIR ? null : join(homedir(), ".agentic", "view-cache")
}
export function readViewCache<T>(name: string): T | null {
  const d = viewCacheDir(); if (!d) return null
  try { return JSON.parse(readFileSync(join(d, `${name}.json`), "utf8")) as T } catch { return null }
}
export function writeViewCache(name: string, v: unknown): void {
  const d = viewCacheDir(); if (!d) return
  try { mkdirSync(d, { recursive: true }); const f = join(d, `${name}.json`); writeFileSync(f + ".tmp", JSON.stringify(v)); renameSync(f + ".tmp", f) } catch { /* a cache */ }
}
