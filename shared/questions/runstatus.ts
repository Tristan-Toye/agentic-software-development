// The on-demand status message for Slack: `status` in a repo channel, a run thread or the DM, or the Status button.
// Pure: the server picks the runs and posts the blocks. Contract: references/owner-questions.md (Dashboard).
import type { Update } from "./runstore.ts"
import { STATUS_ACTION } from "./slack.ts"

export type StatusRun = {
  repo: string; id: string; title: string
  state: string                    // running | waiting | paused | stopped | failed | done | "" (no runner)
  waitingFor?: "stub"              // waiting for the stub being integrated, not for the owner
  phase: string; phaseName: string
  account?: string; started?: string; resumesAt?: string
  context?: number                 // the orchestrator's live context, tokens
  questions: { id: string; text: string }[]
  updates: Update[]                // newest first
  link: string
}

const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
const clip = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s)

export function ago(ts: string, now: number): string {
  const t = Date.parse(ts)
  if (!Number.isFinite(t)) return ""
  const m = Math.max(0, Math.round((now - t) / 60000))
  if (m < 1) return "just now"
  if (m < 60) return `${m} min ago`
  const h = Math.floor(m / 60)
  return `${h}h${String(m % 60).padStart(2, "0")} ago`
}

/**
 * Sub-agents since the runner's current stage began: still running, finished, and finished with a gap or contract
 * change. Agents a workflow launched have no spawn line, only their spawn-done, so they count once they finish.
 */
export function agentCounts(updates: Update[]): { running: number; done: number; flagged: number } {
  const chrono = [...updates].reverse()
  let from = 0
  chrono.forEach((u, i) => { if (u.kind === "runner" && /^stage \S+ start/i.test(u.text)) from = i + 1 })
  const open = new Set<string>()
  const finished = new Set<string>()
  let done = 0, flagged = 0
  for (const u of chrono.slice(from)) {
    if (u.event === "spawn") {
      if (u.status === "running" && u.agent_id) open.add(u.agent_id)
      else if (u.status === "done") { done++; if (u.agent_id) finished.add(u.agent_id) }
    } else if (u.event === "spawn-done" && u.agent_id && !finished.has(u.agent_id)) {
      open.delete(u.agent_id); finished.add(u.agent_id); done++
      if (u.status && u.status !== "done") flagged++
    }
  }
  return { running: open.size, done, flagged }
}

/** True when the newest admission line says the wave waits for agent slots and no agent has started since. */
export function waitingForSlots(updates: Update[]): boolean {
  for (const u of updates) {
    if (u.event === "spawn") return false
    if (u.auto && /^log: ADMISSION-WAIT\b/.test(u.text)) return true
    if (u.auto && /^log: ADMISSION:\s*granted\b/.test(u.text)) return false
  }
  return false
}

const STATE: Record<string, string> = {
  running: "🟢 in progress", waiting: "🟡 waiting for you", paused: "⏸ paused · usage limit",
  queued: "⏳ queued · waiting for a run slot", stopped: "⏹ stopped", failed: "✖ failed", done: "🏁 done",
}
const hhmm = (ts: string) => { const d = new Date(ts); return Number.isFinite(d.getTime()) ? d.toTimeString().slice(0, 5) : "" }

/** One run's status, as Slack mrkdwn. */
export function runStatusText(r: StatusRun, now: number): string {
  const lines: string[] = []
  lines.push(`*<${r.link}|${esc(r.id)}>* — ${esc(clip(r.title, 90))}`)
  const head: string[] = [r.state === "waiting" && r.waitingFor === "stub" ? "⏳ queued · waiting for the stub" : STATE[r.state] ?? (r.state || "not running")]
  if (r.phase && r.phase !== "plan") head.push(`Phase ${esc(r.phase)}${r.phaseName ? ` · ${esc(r.phaseName)}` : ""}`)
  else if (["running", "waiting", "paused", "queued"].includes(r.state)) head.push("starting")
  if (r.state === "paused" && r.resumesAt) head.push(`resumes ${hhmm(r.resumesAt)}`)
  if (r.account) head.push(`account ${esc(r.account)}`)
  if (r.started) head.push(`started ${ago(r.started, now)}`)
  lines.push(head.join(" · "))

  const live = ["running", "waiting"].includes(r.state)
  if (live) {
    const a = agentCounts(r.updates)
    if (a.running || a.done) lines.push(`Agents this stage: ${[a.running ? `${a.running} running` : "", a.done ? `${a.done} finished` : ""].filter(Boolean).join(", ")}${a.flagged ? ` (${a.flagged} with a gap or contract change)` : ""}`)
    if (waitingForSlots(r.updates)) lines.push("⏳ Waiting for free agent slots (another build holds them)")
    const last = r.updates[0]
    if (last) {
      const quiet = (now - Date.parse(last.ts)) / 60000
      lines.push(`Last activity ${ago(last.ts, now)}${r.state === "running" && quiet >= 20 ? " ⚠ quiet" : ""}${r.context ? ` · context ${Math.round(r.context / 1000)}k / 200k` : ""}`)
    }
  }
  const note = r.updates.find((u) => u.kind === "update" && !u.auto && u.event !== "owner-message" && u.text.trim())
  if (note) {
    const para = note.text.trim().split(/\n\s*\n/)[0].replace(/\s*\n\s*/g, " ")
    lines.push(`_Last note (${ago(note.ts, now)}):_ ${esc(clip(para, 400))}`)
  }
  for (const q of r.questions) lines.push(`❓ *${esc(q.id)}* ${esc(clip(q.text, 200))}`)
  return lines.join("\n")
}

/** The whole message: a heading, one section per run, and a Refresh button carrying the same scope. */
export function statusBlocks(runs: StatusRun[], scope: { repo?: string; id?: string }, now: number): { text: string; blocks: any[] } {
  const where = scope.id ? `${scope.repo ? `${scope.repo} · ` : ""}${scope.id}` : scope.repo ? scope.repo : "all repos"
  const heading = `*Status — ${esc(where)}* · ${new Date(now).toTimeString().slice(0, 5)}`
  const blocks: any[] = [{ type: "section", text: { type: "mrkdwn", text: heading } }]
  if (!runs.length) {
    blocks.push({ type: "section", text: { type: "mrkdwn", text: scope.id ? `No dossier ${esc(scope.id)} found.` : "No dossier is running." } })
  }
  for (const r of runs.slice(0, 20)) {
    blocks.push({ type: "divider" })
    blocks.push({ type: "section", text: { type: "mrkdwn", text: clip(runStatusText(r, now), 2900) } })
  }
  blocks.push({
    type: "actions",
    elements: [{ type: "button", action_id: STATUS_ACTION, text: { type: "plain_text", text: "↻ Refresh" }, value: `${scope.repo ?? ""}|${scope.id ?? ""}` }],
  })
  const text = runs.length ? `Status: ${runs.map((r) => `${r.id} ${r.state || "not running"}`).join(", ")}` : "Status: nothing running"
  return { text, blocks }
}

/** The opener of a run thread, with a Status button for that run. */
export function openerBlocks(text: string, repo: string, id: string): any[] {
  return [
    { type: "section", text: { type: "mrkdwn", text } },
    { type: "actions", elements: [{ type: "button", action_id: STATUS_ACTION, text: { type: "plain_text", text: "Status" }, value: `${repo}|${id}` }] },
  ]
}
