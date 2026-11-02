// Run updates store, runner state, dossier summary and a tiny markdown renderer. Contract: references/runner.md, references/owner-questions.md (Dashboard).
import { readFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

export type Update = { ts: string; kind: "runner" | "stage" | "update"; text: string; stage?: string; state?: string }
export type RunnerState = { state: "running" | "waiting" | "done" | "failed" | "stopped"; stage: string; pid: number; started: string }

export const runsDir = (): string => process.env.ASD_RUNS_DIR || join(homedir(), ".agentic", "runs")

/** Updates of `<repo>.<ID>`, newest first; torn or foreign lines are skipped. */
export function readUpdates(key: string, dir = runsDir()): Update[] {
  let text = ""
  try { text = readFileSync(join(dir, `${key}.jsonl`), "utf8") } catch { return [] }
  const out: Update[] = []
  for (const l of text.split("\n")) {
    if (!l.trim()) continue
    try {
      const o = JSON.parse(l)
      if (typeof o?.text !== "string" || !["runner", "stage", "update"].includes(o.kind)) continue
      out.push({ ts: String(o.ts ?? ""), kind: o.kind, text: o.text, stage: typeof o.stage === "string" ? o.stage : undefined, state: typeof o.state === "string" ? o.state : undefined })
    } catch { /* torn line */ }
  }
  return out.reverse().slice(0, 50)
}

export function pidAlive(pid: number): boolean {
  if (!pid || pid < 1) return false
  try { process.kill(pid, 0); return true } catch (e) { return (e as NodeJS.ErrnoException).code === "EPERM" }
}

/** `<key>.runner.json`; a running or waiting state with a dead pid reads as stopped. null when absent. */
export function readRunner(key: string, dir = runsDir(), alive: (pid: number) => boolean = pidAlive): RunnerState | null {
  try {
    const j = JSON.parse(readFileSync(join(dir, `${key}.runner.json`), "utf8"))
    let state = ["running", "waiting", "done", "failed", "stopped"].includes(j.state) ? j.state : "stopped"
    if ((state === "running" || state === "waiting") && !alive(Number(j.pid))) state = "stopped"
    return { state, stage: String(j.stage ?? ""), pid: Number(j.pid) || 0, started: String(j.started ?? "") }
  } catch { return null }
}
export const runnerActive = (r: RunnerState | null): boolean => r?.state === "running" || r?.state === "waiting"

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
