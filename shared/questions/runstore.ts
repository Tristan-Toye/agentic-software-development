// Run updates store, runner state, dossier summary and a tiny markdown renderer. Contract: references/runner.md, references/owner-questions.md (Dashboard).
import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

export type Update = { ts: string; kind: "runner" | "stage" | "update"; text: string; stage?: string; state?: string; auto?: boolean; event?: string; status?: string; agent_id?: string }
export type RunnerState = { state: "running" | "waiting" | "paused" | "done" | "failed" | "stopped"; stage: string; pid: number; started: string; root: string; account?: string; resumesAt?: string; command?: string; session?: string }

export const runsDir = (): string => process.env.ASD_RUNS_DIR || join(homedir(), ".agentic", "runs")

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
    let state = ["running", "waiting", "paused", "done", "failed", "stopped"].includes(j.state) ? j.state : "stopped"
    if ((state === "running" || state === "waiting" || state === "paused") && !alive(Number(j.pid))) state = "stopped"
    return { state, stage: String(j.stage ?? ""), pid: Number(j.pid) || 0, started: String(j.started ?? ""), root: typeof j.root === "string" ? j.root : "", ...(typeof j.account === "string" ? { account: j.account } : {}), ...(typeof j.resumes_at === "string" ? { resumesAt: j.resumes_at } : {}), ...(typeof j.command === "string" ? { command: j.command } : {}), ...(typeof j.session === "string" && j.session ? { session: j.session } : {}) }
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
export function readBlocks(key: string, dir = runsDir(), limit = 5): Block[] {
  let lines: any[] = []
  try { lines = readFileSync(join(dir, `${key}.jsonl`), "utf8").split("\n").filter((l) => l.includes('"event":"block')).map((l) => { try { return JSON.parse(l) } catch { return null } }).filter(Boolean) } catch { return [] }
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
  return ""
}
export const runnerActive = (r: RunnerState | null): boolean => r?.state === "running" || r?.state === "waiting" || r?.state === "paused"

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
const inline = (s: string): string => esc(s).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")

/** Minimal read-only markdown: headings, paragraphs, lists, code fences, inline code, tables as preformatted. Everything escaped. */
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
      out.push(`<pre>${esc(t.join("\n"))}</pre>`)
    } else if (/^#{1,6}\s/.test(l)) {
      const n = l.match(/^#+/)![0].length
      out.push(`<h${n}>${inline(l.replace(/^#+\s*/, ""))}</h${n}>`); i++
    } else if (/^\s*([-*]|\d+\.)\s/.test(l)) {
      const items: string[] = []
      for (; i < lines.length && /^\s*([-*]|\d+\.)\s/.test(lines[i]); i++) {
        let item = lines[i].replace(/^\s*([-*]|\d+\.)\s/, "")
        while (i + 1 < lines.length && /^\s{2,}\S/.test(lines[i + 1]) && !/^\s*([-*]|\d+\.)\s/.test(lines[i + 1])) item += " " + lines[++i].trim()
        items.push(`<li>${inline(item)}</li>`)
      }
      out.push(`<ul>${items.join("")}</ul>`)
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
