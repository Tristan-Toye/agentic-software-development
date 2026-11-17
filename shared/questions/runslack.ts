// Slack progress thread per run: which updates-store line becomes a Slack message (pure) and the thread sidecar.
// Contract: references/owner-questions.md (Dashboard). No network here; the server posts what these functions select.
import { readFileSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import type { Update } from "./runstore.ts"

export type RunSlackMsg = { role: "open" | "reply"; text: string }

const clip = (s: string, n = 300): string => (s.length > n ? s.slice(0, n - 1) + "…" : s)
const firstLine = (s: string): string => s.split("\n").map((l) => l.trim()).find(Boolean) ?? ""

/** `ASD_RUN_SLACK=0` turns the progress thread off. */
export const runSlackEnabled = (): boolean => process.env.ASD_RUN_SLACK !== "0"

/**
 * The Slack message one updates-store line becomes, or null. The first `runner` start line opens the thread
 * ("▶ repo · ID — title"); stage-done, waiting, answered, failed and done lines reply; `auto` routine lines never post.
 * `link` is the run view of the dossier.
 */
export function slackMessageFor(u: Update, o: { repo: string; id: string; title: string; link: string }): RunSlackMsg | null {
  if (u.auto === true) return null
  const t = u.text.trim()
  const tail = ` <${o.link}|run view>`
  if (u.kind === "runner") {
    if (/^start\b/i.test(t)) return { role: "open", text: `▶ ${o.repo} · ${o.id}${o.title ? ` — ${o.title}` : ""}${tail}` }
    if (/^waiting\b/i.test(t)) return { role: "reply", text: `⏳ Waiting on you: ${clip(t.replace(/^waiting on\s*/i, ""))}${tail}` }
    if (/^(answered|follow-up)\b/i.test(t)) return { role: "reply", text: `✅ ${clip(t)}` }
    if (/^paused\b/i.test(t)) return { role: "reply", text: `⏸ ${clip(t.replace(/^paused:\s*/i, "Paused: "))}${tail}` }
    if (/^switched to account\b/i.test(t)) return { role: "reply", text: `🔀 ${clip(t.charAt(0).toUpperCase() + t.slice(1))}` }
    if (/^resumed\b/i.test(t)) return { role: "reply", text: `▶ ${clip(t.charAt(0).toUpperCase() + t.slice(1))}` }
    if (u.state === "failed" || /\bfailed\b/i.test(t)) return { role: "reply", text: `✖ ${clip(t)}${tail}` }
    const dm = t.match(/^deferred report: (\d+) issue/i)
    if (dm) return Number(dm[1]) ? { role: "reply", text: `📋 ${dm[1]} deferred issue${dm[1] === "1" ? "" : "s"} — fix them from the run view${tail}` } : null
    if (u.state === "done" || /^done\b/i.test(t)) return { role: "reply", text: `🏁 Done${tail}` }
    return null
  }
  if (u.kind === "stage") return { role: "reply", text: `✔ Stage done${u.stage ? ` (${u.stage})` : ""}: ${clip(firstLine(t))}` }
  return null
}

/**
 * The messages to post for the lines newer than the first `seen` ones of one run (oldest first). With no thread yet,
 * messages before the opening start line are dropped, so a thread always begins with its opener.
 */
export function pendingSlack(chronological: Update[], seen: number, hasThread: boolean, o: { repo: string; id: string; title: string; link: string }): { upto: number; msgs: RunSlackMsg[] } {
  const msgs: RunSlackMsg[] = []
  let open = hasThread
  for (const u of chronological.slice(seen)) {
    const m = slackMessageFor(u, o)
    if (!m) continue
    if (m.role === "open") { if (open) continue; open = true; msgs.push(m); continue }
    if (open) msgs.push(m)
  }
  return { upto: chronological.length, msgs }
}

export type RunThread = { channel: string; ts: string; seen?: number }
/** `<key>.slack.json` in the runs dir, or null. */
export function readRunThread(dir: string, key: string): RunThread | null {
  try {
    const j = JSON.parse(readFileSync(join(dir, `${key}.slack.json`), "utf8"))
    return typeof j?.channel === "string" && typeof j?.ts === "string" ? { channel: j.channel, ts: j.ts, seen: typeof j.seen === "number" ? j.seen : undefined } : null
  } catch { return null }
}
export function writeRunThread(dir: string, key: string, t: RunThread): void {
  try { writeFileSync(join(dir, `${key}.slack.json`), JSON.stringify(t) + "\n") } catch { /* best effort */ }
}

/** True when the sidecar must be (re)written: a rewrite of identical content fires the runs-dir watch and rescans, forever. */
export function threadChanged(side: RunThread | null, thread: RunThread, upto: number): boolean {
  return !side || side.channel !== thread.channel || side.ts !== thread.ts || side.seen !== upto
}

/** A runs-dir file name whose change must not wake the dashboard: the server's own sidecars and temp files. */
export function runsWatchIgnored(name: string): boolean {
  return /\.slack\.json$|\.tmp$/.test(name)
}
