// Owner-questions server. Run: bun --no-install shared/questions/server.ts
// Contract: references/owner-questions.md
import { watch } from "node:fs"
import { AnsweredError, appendOwner, idForThread, readAll, readQuestion, readSidecar, storeDir, writeSidecar, type Line, type Question } from "./store.ts"
import { PAGE } from "./page.ts"
import { api, keychain, pageLink, POINTER_TEXT, questionBlocks, runSocket, type OwnerAction } from "./slack.ts"

const DIR = storeDir()
const REMIND_MS = 2 * 3600 * 1000
let PORT = 0
const log = (m: string) => console.log(`[asd-questions] ${m}`)

/** Show a macOS notification; the text goes through argv so no AppleScript escaping can break out. */
export function notify(title: string, subtitle: string, body: string): void {
  if (process.env.ASD_QUESTIONS_NO_NOTIFY === "1") return
  const flat = (s: string) => s.replace(/\s+/g, " ").slice(0, 200)
  const script = 'on run argv\ndisplay notification (item 3 of argv) with title (item 1 of argv) subtitle (item 2 of argv)\nend run'
  try {
    const proc = Bun.spawn(["osascript", "-e", script, flat(title), flat(subtitle), flat(body)], { stdout: "ignore", stderr: "pipe" })
    proc.exited.then(async (code) => {
      if (code !== 0) log(`notification failed: osascript exit ${code}: ${(await new Response(proc.stderr).text()).trim().slice(0, 300)}`)
      else log(`notified: ${flat(title)}`)
    })
  } catch (e) {
    log(`notification failed: ${(e as Error).message}`)
  }
}

// ---- Slack state ----
let botToken = ""
let slackChannel = ""
const owner = process.env.ASD_SLACK_USER ?? ""

async function dmChannel(): Promise<string> {
  if (!slackChannel) slackChannel = (await api("conversations.open", botToken, { users: owner })).channel.id
  return slackChannel
}
const slackOn = () => botToken !== ""

async function slackPostQuestion(q: Question): Promise<void> {
  const channel = await dmChannel()
  const r = await api("chat.postMessage", botToken, {
    channel,
    text: `${q.repo} · ${q.dossier}: ${q.text}`,
    blocks: questionBlocks(q, PORT),
  })
  writeSidecar(DIR, q.id, { channel: r.channel ?? channel, ts: r.ts })
}
async function slackThread(id: string, text: string): Promise<void> {
  const s = readSidecar(DIR, id)
  if (!s) return
  await api("chat.postMessage", botToken, { channel: s.channel, thread_ts: s.ts, text })
}
async function slackMarkAnswered(q: Question): Promise<void> {
  const s = readSidecar(DIR, q.id)
  if (!s) return
  await api("chat.update", botToken, { channel: s.channel, ts: s.ts, text: `${q.repo} · ${q.dossier}: answered`, blocks: questionBlocks(q, PORT, true) })
  await api("reactions.add", botToken, { channel: s.channel, timestamp: s.ts, name: "white_check_mark" }).catch((e) => {
    if (!/already_reacted/.test(String(e))) throw e
  })
}

// ---- Watch + notify ----
type Seen = { n: number; remindedAt: number; marked: boolean }
const seen = new Map<string, Seen>()
const startedAt = Date.now()
let scanning = false
let again = false

async function safe(p: Promise<unknown>, what: string): Promise<void> {
  try {
    await p
  } catch (e) {
    log(`${what}: ${(e as Error).message}`)
  }
}

export async function scan(): Promise<void> {
  if (scanning) return void (again = true)
  scanning = true
  try {
    for (const q of readAll(DIR)) {
      const s = seen.get(q.id)
      if (!s) {
        const side = readSidecar(DIR, q.id)
        seen.set(q.id, { n: q.lines.length, remindedAt: q.state === "open" ? (side ? startedAt : Date.now()) : 0, marked: q.state === "answered" })
        if (q.state === "open" && !side) {
          notify(`${q.repo} · ${q.dossier}`, q.id, q.text)
          if (slackOn()) await safe(slackPostQuestion(q), "slack post question")
        }
        continue
      }
      const fresh = q.lines.slice(s.n)
      s.n = q.lines.length
      for (const l of fresh) {
        if (l.from === "agent" && l.kind === "reply" && slackOn()) await safe(slackThread(q.id, l.text), "slack reply")
        else if (l.from === "owner" && l.via !== "slack" && slackOn()) {
          await safe(slackThread(q.id, `*Owner (${l.via ?? "cli"}) ${l.kind}${l.option ? ` ${l.option}` : ""}:* ${l.text}`), "slack mirror")
        }
      }
      if (q.state === "answered" && !s.marked) {
        s.marked = true
        if (slackOn()) await safe(slackMarkAnswered(q), "slack mark answered")
      }
      if (q.state === "open") {
        const last = q.lines[q.lines.length - 1]
        const now = Date.now()
        if (last.from === "agent" && now - Date.parse(last.ts) > REMIND_MS && now - s.remindedAt > REMIND_MS) {
          s.remindedAt = now
          notify(`Still waiting: ${q.repo} · ${q.dossier}`, q.id, q.text)
          if (slackOn()) await safe(slackThread(q.id, `Reminder: still waiting for your answer. ${pageLink(PORT, q.id)}`), "slack reminder")
        }
      }
    }
  } finally {
    scanning = false
    if (again) {
      again = false
      await scan()
    }
  }
}

// ---- Slack inbound ----
async function onSlackAction(a: OwnerAction): Promise<void> {
  if (a.type === "pointer") {
    await api("chat.postMessage", botToken, { channel: a.channel, thread_ts: a.threadTs, text: POINTER_TEXT })
    return
  }
  if (a.type !== "owner") return
  const q = readQuestion(DIR, a.id)
  if (!q) return
  let text = a.text
  if (a.option) {
    const o = q.options.find((x) => x.key === a.option)
    if (!o) return
    text = o.label
  }
  try {
    appendOwner(DIR, a.id, { kind: a.kind, text, option: a.option, via: "slack" })
  } catch (e) {
    if (e instanceof AnsweredError && a.threadTs && a.channel) {
      await api("chat.postMessage", botToken, { channel: a.channel, thread_ts: a.threadTs, text: "Already answered; the first answer is final." })
    }
    return
  }
  await scan()
}

// ---- HTTP ----
const json = (body: unknown, status = 200) => Response.json(body, { status })

export async function handle(req: Request): Promise<Response> {
  const url = new URL(req.url)
  const host = req.headers.get("host") ?? ""
  if (!/^(127\.0\.0\.1|localhost)(:\d+)?$/.test(host)) return json({ error: "bad host" }, 403)
  const path = url.pathname
  if (req.method === "GET" && path === "/") return new Response(PAGE, { headers: { "content-type": "text/html; charset=utf-8" } })
  if (req.method === "GET" && path === "/api/questions") {
    const all = url.searchParams.get("state") === "open" ? readAll(DIR).filter((q) => q.state === "open") : readAll(DIR)
    return json(all)
  }
  const m = path.match(/^\/api\/questions\/([\w.-]+)(\/messages)?$/)
  if (!m) return json({ error: "not found" }, 404)
  const q = readQuestion(DIR, m[1])
  if (!q) return json({ error: "no such question" }, 404)
  if (req.method === "GET" && !m[2]) return json(q)
  if (req.method === "POST" && m[2]) {
    const origin = req.headers.get("origin")
    if (origin && origin !== `http://${host}`) return json({ error: "bad origin" }, 403)
    let b: any
    try {
      b = await req.json()
    } catch {
      return json({ error: "body must be JSON" }, 400)
    }
    const kind = b?.kind
    if (kind !== "followup" && kind !== "answer") return json({ error: "kind must be followup or answer" }, 400)
    const option = typeof b.option === "string" ? b.option : ""
    if (option && !q.options.some((o) => o.key === option)) return json({ error: "unknown option" }, 400)
    if (kind === "followup" && option) return json({ error: "a follow-up has no option" }, 400)
    let text = typeof b.text === "string" ? b.text.trim() : ""
    if (!text && option) text = q.options.find((o) => o.key === option)!.label
    if (!text) return json({ error: "text is required" }, 400)
    try {
      const after = appendOwner(DIR, q.id, { kind, text, option, via: "page" })
      void scan()
      return json(after, 201)
    } catch (e) {
      if (e instanceof AnsweredError) return json({ error: "already answered" }, 409)
      return json({ error: (e as Error).message }, 500)
    }
  }
  return json({ error: "method not allowed" }, 405)
}

async function main(): Promise<void> {
  const requested = process.env.ASD_QUESTIONS_PORT
  const server = Bun.serve({ hostname: "127.0.0.1", port: requested !== undefined && requested !== "" ? Number(requested) : 7317, fetch: handle })
  PORT = server.port ?? 7317
  console.log(`listening http://127.0.0.1:${PORT}`)

  if (owner && process.env.ASD_QUESTIONS_NO_SLACK !== "1") {
    botToken = keychain("asd-slack-bot")
    const appToken = keychain("asd-slack-app")
    if (botToken && appToken) {
      void runSocket(appToken, { onAction: onSlackAction, lookup: (c, t) => idForThread(DIR, c, t), ownerUser: owner, log })
      log("slack enabled")
    } else {
      botToken = ""
      log("slack disabled: bot or app token missing in the keychain")
    }
  } else {
    log("slack disabled: no ASD_SLACK_USER (or ASD_QUESTIONS_NO_SLACK=1)")
  }

  await scan()
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    watch(DIR, () => {
      clearTimeout(timer)
      timer = setTimeout(() => void scan(), 150)
    })
  } catch (e) {
    log(`fs.watch unavailable, rescan only: ${(e as Error).message}`)
  }
  setInterval(() => void scan(), 10_000)
}

if (import.meta.main) await main()
