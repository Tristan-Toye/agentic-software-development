// Slack, Socket Mode without Bolt. Envelope handling is pure; I/O lives below it.
import type { Question } from "./store.ts"

export type OwnerAction =
  | { type: "owner"; id: string; kind: "answer" | "followup"; text: string; option?: string; channel?: string; threadTs?: string }
  | { type: "pointer"; channel: string; threadTs?: string }
  | { type: "status"; channel: string; ts?: string; threadTs?: string; id?: string; repo?: string; refresh?: boolean }
  | { type: "reconnect" }
  | { type: "hello" }
  | { type: "none" }

export type Interpreted = { ack: { envelope_id: string } | null; action: OwnerAction }

/**
 * Turn one Socket Mode envelope into the ack to send and the action to take.
 * `lookup(channel, threadTs)` maps a thread root to a question id (via the sidecars).
 * `ownerUser` (optional) restricts events to that member.
 */
export function interpretEnvelope(
  env: any,
  lookup: (channel: string, threadTs: string) => string | null,
  ownerUser = "",
): Interpreted {
  const ack = typeof env?.envelope_id === "string" ? { envelope_id: env.envelope_id } : null
  const none: Interpreted = { ack, action: { type: "none" } }
  switch (env?.type) {
    case "hello":
      return { ack, action: { type: "hello" } }
    case "disconnect":
      return { ack, action: { type: "reconnect" } }
    case "events_api": {
      const ev = env.payload?.event
      if (!ev || ev.type !== "message" || !["im", "group", "channel"].includes(ev.channel_type)) return none
      if (ev.bot_id || ev.subtype) return none
      if (ownerUser && ev.user && ev.user !== ownerUser) return none
      const text = String(ev.text ?? "").trim()
      if (!text) return none
      const threadTs: string | undefined = ev.thread_ts && ev.thread_ts !== ev.ts ? ev.thread_ts : undefined
      const id = threadTs ? lookup(ev.channel, threadTs) : null
      // "status" (or "status W-128") outside a question's thread asks for a run update.
      const st = !id ? text.match(STATUS_RE) : null
      if (st) return { ack, action: { type: "status", channel: ev.channel, ts: ev.ts, threadTs, id: st[1]?.toUpperCase() } }
      // In a repo channel other talk is not ours: only a DM outside a question's thread gets the pointer.
      if (!id) return ev.channel_type === "im" ? { ack, action: { type: "pointer", channel: ev.channel, threadTs } } : { ack, action: { type: "none" } }
      const k = replyKind(text)
      return { ack, action: { type: "owner", id, kind: k.kind, text: k.text, channel: ev.channel, threadTs } }
    }
    case "interactive": {
      const p = env.payload
      if (p?.type !== "block_actions") return none
      if (ownerUser && p.user?.id && p.user.id !== ownerUser) return none
      const value = String(p.actions?.[0]?.value ?? "")
      if (p.actions?.[0]?.action_id === STATUS_ACTION) {
        // value: "<repo>|<ID>" (one run), "<repo>|" (the repo's runs) or "|" (every running dossier).
        const [repo = "", id = ""] = value.split("|")
        return { ack, action: { type: "status", channel: p.channel?.id ?? "", ts: p.container?.message_ts ?? p.message?.ts, threadTs: p.message?.thread_ts, repo: repo || undefined, id: id || undefined, refresh: isStatusMessage(p.message) } }
      }
      const i = value.lastIndexOf("|")
      if (i < 1) return none
      return {
        ack,
        action: { type: "owner", id: value.slice(0, i), kind: "answer", text: "", option: value.slice(i + 1), channel: p.channel?.id },
      }
    }
    default:
      return none
  }
}

/** `status`, `update`, `?` or `status W-128`, alone on the message. */
export const STATUS_RE = /^(?:status|update|updates|\?)(?:\s+([A-Za-z]+-\d+))?\s*[.!?]*$/i
export const STATUS_ACTION = "run_status"
/** A status message (its first section is the "*Status —" heading): its Refresh button updates it in place. */
export const isStatusMessage = (m: any): boolean => String(m?.blocks?.[0]?.text?.text ?? "").startsWith("*Status —")

/**
 * What a free-text reply to a question is: one that ends in "?" asks the agent a follow-up (the question stays open),
 * anything else answers it. `answer:` or `ask:` in front says it outright. The page and Slack read replies the same way.
 */
export function replyKind(raw: string): { kind: "answer" | "followup"; text: string } {
  const t = raw.trim()
  const a = t.match(/^answer:\s*([\s\S]*)$/i)
  if (a) return { kind: "answer", text: a[1].trim() }
  const q = t.match(/^ask:\s*([\s\S]*)$/i)
  if (q) return { kind: "followup", text: q[1].trim() }
  return { kind: /\?\s*$/.test(t) ? "followup" : "answer", text: t }
}
export const POINTER_TEXT = "Reply in a question's thread: a reply ending in `?` asks the agent a follow-up, anything else answers it (`ask:` or `answer:` in front to say it outright). Send `status` (or `status W-128`) for an update on the running dossiers."

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
}
const clip = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s)

export function pageLink(port: number | string, id: string): string {
  return `http://127.0.0.1:${port}/#${id}`
}

export function questionBlocks(q: Question, port: number | string, answered = false): any[] {
  const opts = q.options
    .map((o) => `*${esc(o.key)}* ${esc(o.label)}${o.consequence ? ` — ${esc(o.consequence)}` : ""}`)
    .join("\n")
  let body = `*${esc(q.repo)} · ${esc(q.dossier)}*\n${esc(q.text)}`
  if (q.blocks) body += `\n_Blocks:_ ${esc(q.blocks)}`
  if (opts) body += `\n\n${opts}`
  if (q.recommendation) body += `\n\n_Recommended:_ *${esc(q.recommendation)}*`
  if (answered && q.answer) {
    body += `\n\n:white_check_mark: *Answered${q.answer.option ? ` (${esc(String(q.answer.option))})` : ""}:* ${esc(q.answer.text)}`
  }
  const blocks: any[] = [{ type: "section", text: { type: "mrkdwn", text: clip(body, 2900) } }]
  if (!answered && q.options.length) {
    blocks.push({
      type: "actions",
      elements: q.options.map((o) => ({
        type: "button",
        action_id: `opt_${o.key}`,
        text: { type: "plain_text", text: clip(`${o.key}: ${o.label}`, 75) },
        value: `${q.id}|${o.key}`,
      })),
    })
  }
  blocks.push({
    type: "context",
    elements: [{ type: "mrkdwn", text: `${esc(q.repo)} / ${esc(q.dossier)} / \`${esc(q.id)}\` · <${pageLink(port, q.id)}|open page>` }],
  })
  return blocks
}

export function keychain(service: string): string {
  try {
    const r = Bun.spawnSync(["security", "find-generic-password", "-s", service, "-w"], { stdout: "pipe", stderr: "ignore" })
    return r.exitCode === 0 ? r.stdout.toString().trim() : ""
  } catch {
    return ""
  }
}

/** The channel name for a repo: Slack allows lower-case letters, digits, - and _, under 80 characters. */
export function channelNameFor(repo: string): string {
  const n = repo.toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "")
  return (n || "pipeline").slice(0, 79)
}

/** A read method with query parameters (conversations.list takes no JSON body). */
export async function apiGet(method: string, token: string, params: Record<string, string>): Promise<any> {
  const res = await fetch(`https://slack.com/api/${method}?${new URLSearchParams(params)}`, { headers: { Authorization: `Bearer ${token}` } })
  const j: any = await res.json().catch(() => ({}))
  if (!j.ok) throw new Error(`slack ${method}: ${j.error ?? res.status}`)
  return j
}

export async function api(method: string, token: string, body?: unknown): Promise<any> {
  const res = await fetch(`https://slack.com/api/${method}`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json; charset=utf-8" },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const j: any = await res.json().catch(() => ({}))
  if (!j.ok) throw new Error(`slack ${method}: ${j.error ?? res.status}`)
  return j
}

export type Handlers = {
  onAction: (a: OwnerAction) => Promise<void> | void
  lookup: (channel: string, threadTs: string) => string | null
  ownerUser: string
  log: (m: string) => void
}

/** Run the Socket Mode loop forever, reconnecting with backoff (1 s doubling to 60 s). */
export async function runSocket(appToken: string, h: Handlers): Promise<never> {
  let delay = 1000
  for (;;) {
    try {
      const open = await api("apps.connections.open", appToken)
      await new Promise<void>((resolve) => {
        const ws = new WebSocket(open.url)
        ws.onmessage = async (e) => {
          let env: any
          try {
            env = JSON.parse(String(e.data))
          } catch {
            return
          }
          const ev = env?.payload?.event
          h.log(`slack envelope ${env?.type}${ev ? ` event=${ev.type} channel_type=${ev.channel_type} subtype=${ev.subtype ?? "-"} thread=${ev.thread_ts ? "yes" : "no"} bot=${ev.bot_id ? "yes" : "no"}` : ""}`)
          const { ack, action } = interpretEnvelope(env, h.lookup, h.ownerUser)
          if (action.type !== "none" && action.type !== "hello") h.log(`slack action ${action.type}`)
          if (ack) ws.send(JSON.stringify(ack))
          if (action.type === "hello") delay = 1000
          else if (action.type === "reconnect") ws.close()
          else if (action.type !== "none") {
            try {
              await h.onAction(action)
            } catch (err) {
              h.log(`slack action failed: ${(err as Error).message}`)
            }
          }
        }
        ws.onclose = () => resolve()
        ws.onerror = () => resolve()
      })
      h.log("slack socket closed, reconnecting")
    } catch (err) {
      h.log(`slack connect failed: ${(err as Error).message}`)
    }
    await Bun.sleep(delay)
    delay = Math.min(delay * 2, 60000)
  }
}
