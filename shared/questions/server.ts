// Owner-questions server. Run: bun --no-install shared/questions/server.ts
// Contract: references/owner-questions.md
import { mkdirSync, watch } from "node:fs"
import { AnsweredError, appendOwner, idForThread, readAll, readQuestion, readSidecar, storeDir, writeSidecar, type Line, type Question } from "./store.ts"
import { PAGE } from "./page.ts"
import { buildLog, getRuns, PHASE_NAMES, questionRepo, type Run } from "./runs.ts"
import { getStubs, openWorkHtml, recordFile } from "./stubs.ts"
import { dossierPage, readUpdates, renderMarkdown, runsDir } from "./runstore.ts"
import { emitChange, subscribe } from "./events.ts"
import { groupByPhase, logEntries, mergeTimeline, nowPanel, parseRecord } from "./timeline.ts"
import { runRunner } from "./controls.ts"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { pendingSlack, readRunThread, runSlackEnabled, writeRunThread } from "./runslack.ts"
import { api, apiGet, channelNameFor, keychain, pageLink, POINTER_TEXT, questionBlocks, runSocket, type OwnerAction } from "./slack.ts"

const DIR = storeDir()
const MIN = 60 * 1000
/** Reminders due after `elapsedMs` of waiting: at 30 min, at 1 h 30 (1 h later), then every 2 h. */
export function remindersDue(elapsedMs: number): number {
  if (elapsedMs < 30 * MIN) return 0
  if (elapsedMs < 90 * MIN) return 1
  return 2 + Math.floor((elapsedMs - 90 * MIN) / (120 * MIN))
}
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

/* One private channel per repo, named after it, created on first use with the
   owner invited; everything about that repo (questions, run threads) goes
   there. A workspace without the channel scopes falls back to the DM. */
const repoChannels = new Map<string, string>()
async function findChannel(name: string): Promise<{ id: string; member: boolean } | null> {
  let cursor = ""
  for (let page = 0; page < 20; page++) {
    const r = await apiGet("conversations.list", botToken, { types: "private_channel,public_channel", exclude_archived: "true", limit: "999", ...(cursor ? { cursor } : {}) })
    const c = (r.channels ?? []).find((x: any) => x.name === name)
    if (c) return { id: c.id, member: !!c.is_member }
    cursor = r.response_metadata?.next_cursor ?? ""
    if (!cursor) return null
  }
  return null
}
async function repoChannel(repo: string): Promise<string> {
  const hit = repoChannels.get(repo)
  if (hit) return hit
  try {
    const base = channelNameFor(repo)
    let id = ""
    for (const name of [base, `${base}-pipeline`.slice(0, 79)]) {
      const found = await findChannel(name)
      if (found?.member) { id = found.id; break }
      if (found) continue                       // the name is someone else's channel: try the next
      try { id = (await api("conversations.create", botToken, { name, is_private: true })).channel.id; log(`slack channel #${name} created for ${repo}`); break }
      catch (e) { if (!/name_taken/.test(String(e))) throw e }
    }
    if (!id) throw new Error("no channel name left")
    if (owner) await api("conversations.invite", botToken, { channel: id, users: owner }).catch((e) => {
      if (!/already_in_channel|cant_invite_self/.test(String(e))) throw e
    })
    repoChannels.set(repo, id)
    return id
  } catch (e) {
    log(`slack repo channel for ${repo} unavailable (${(e as Error).message}); using the DM`)
    return dmChannel()
  }
}

async function slackPostQuestion(q: Question): Promise<void> {
  const channel = await repoChannel(q.repo)
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
// `since`: ts of the agent line the reminders count from; `reminders`: how many were sent for it.
type Seen = { n: number; since: string; reminders: number; marked: boolean }
const seen = new Map<string, Seen>()
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
        const last = q.lines[q.lines.length - 1]
        // A question already posted before this start is caught up without a burst of reminders.
        const caughtUp = side && last.from === "agent" ? remindersDue(Date.now() - Date.parse(last.ts)) : 0
        seen.set(q.id, { n: q.lines.length, since: last.ts, reminders: caughtUp, marked: q.state === "answered" })
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
        if (last.from !== "agent") continue
        if (last.ts !== s.since) { s.since = last.ts; s.reminders = 0 }
        if (remindersDue(now - Date.parse(last.ts)) > s.reminders) {
          s.reminders = remindersDue(now - Date.parse(last.ts))
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

// ---- Slack progress thread per run ----
const runSeen = new Map<string, number>()
let scanningRuns = false
/** The agent's own notes are markdown (bullets, tables): render them for the timeline. */
function withHtml<T extends { type: string; auto?: boolean; text: string; html?: string }>(es: T[]): T[] {
  for (const e of es) if (e.type === "update" && !e.auto && e.text) e.html = renderMarkdown(e.text)
  return es
}
const SERVER_START = Date.now()
/** Post the new lines of each run's updates store into its Slack thread; the first start line opens the thread. */
export async function scanRunThreads(): Promise<void> {
  if (!slackOn() || !runSlackEnabled() || scanningRuns) return
  scanningRuns = true
  try {
    for (const r of await allRuns()) {
      const key = `${r.repo}.${r.id}`
      const chrono = readUpdates(key, runsDir(), 100000).reverse()
      if (!chrono.length) continue
      const side = readRunThread(runsDir(), key)
      // A run first seen without a thread: one that began before this server
      // started is caught up silently (no backlog after a restart); one that
      // began since is new and gets its thread from its first line.
      if (!runSeen.has(key)) {
        const began = Date.parse(String(chrono[0]?.ts ?? ""))
        const old = !side && !(began >= SERVER_START - 60_000)
        runSeen.set(key, side?.seen ?? (old ? chrono.length : 0))
        if (old) continue
      }
      const link = `http://127.0.0.1:${PORT}/repo/${encodeURIComponent(r.repo)}/${encodeURIComponent(r.id)}`
      const { upto, msgs } = pendingSlack(chrono, runSeen.get(key)!, !!side, { repo: r.repo, id: r.id, title: r.title, link })
      let thread = side
      for (const m of msgs) {
        try {
          if (m.role === "open") {
            const res = await api("chat.postMessage", botToken, { channel: await repoChannel(r.repo), text: m.text })
            thread = { channel: res.channel ?? slackChannel, ts: res.ts }
          } else if (thread) await api("chat.postMessage", botToken, { channel: thread.channel, thread_ts: thread.ts, text: m.text })
        } catch (e) { log(`slack run thread: ${(e as Error).message}`) }
      }
      runSeen.set(key, upto)
      if (thread) writeRunThread(runsDir(), key, { ...thread, seen: upto })
    }
  } finally { scanningRuns = false }
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
const html = (body: string) => new Response(body, { headers: { "content-type": "text/html; charset=utf-8" } })
const allRuns = async (): Promise<Run[]> => (await getRuns(() => readAll(DIR))).runs
const findRun = async (repo: string, id: string): Promise<Run | undefined> => (await allRuns()).find((r) => r.repo === repo && r.id === id)

/** Server-Sent Events: a `changed` event with the kinds that changed; a comment heartbeat keeps the stream open. */
function events(req: Request): Response {
  let un = () => {}
  let hb: ReturnType<typeof setInterval> | undefined
  const enc = new TextEncoder()
  const stream = new ReadableStream({
    start(c) {
      const send = (t: string) => { try { c.enqueue(enc.encode(t)) } catch { /* closed */ } }
      send("retry: 3000\n: connected\n\n")
      un = subscribe((what) => send(`event: changed\ndata: ${JSON.stringify({ what })}\n\n`))
      hb = setInterval(() => send(": hb\n\n"), 15_000)
      req.signal.addEventListener("abort", () => { un(); clearInterval(hb) })
    },
    cancel() { un(); clearInterval(hb) },
  })
  return new Response(stream, { headers: { "content-type": "text/event-stream", "cache-control": "no-cache", connection: "keep-alive" } })
}

/** The last 200 lines of a run's `<key>.runner.log`, or null. */
function runnerLog(repo: string, id: string): string | null {
  try { return readFileSync(join(runsDir(), `${repo}.${id}.runner.log`), "utf8").split("\n").slice(-201).join("\n") } catch { return null }
}
const inflightControl = new Set<string>()

export async function handle(req: Request): Promise<Response> {
  const url = new URL(req.url)
  const host = req.headers.get("host") ?? ""
  if (!/^(127\.0\.0\.1|localhost)(:\d+)?$/.test(host)) return json({ error: "bad host" }, 403)
  const path = url.pathname
  const gr = path.match(/^\/repo\/([\w.-]+)\/graph$/)
  if (req.method === "GET" && gr) return new Response(null, { status: 302, headers: { location: `/repo/${gr[1]}` } })
  if (req.method === "GET" && (path === "/" || path === "/questions" || /^\/repo\/[\w.-]+(\/.*)?$/.test(path))) return html(PAGE)
  if (req.method === "GET" && path === "/api/events") return events(req)
  if (req.method === "GET" && path === "/api/runs") return json({ ...(await getRuns(() => readAll(DIR))), slack: slackOn() })
  if (req.method === "GET" && path === "/api/stubs") return json(await getStubs(() => readAll(DIR)))
  const ow = path.match(/^\/open-work\/([\w.-]+)$/)
  if (req.method === "GET" && ow) {
    const html = await openWorkHtml(ow[1], readAll(DIR))
    return html === null ? json({ error: "no open-work report for that repo" }, 404) : new Response(html, { headers: { "content-type": "text/html; charset=utf-8" } })
  }
  const tl = path.match(/^\/api\/timeline\/([\w.-]+)\/([\w.-]+)$/)
  if (req.method === "GET" && tl) {
    const runs = await allRuns()
    const run = runs.find((r) => r.repo === tl[1] && r.id === tl[2])
    if (!run) return json({ error: "no such dossier" }, 404)
    let log: string[] = []
    try { log = logEntries(buildLog(readFileSync(run.file, "utf8"))) } catch { /* unreadable */ }
    const updates = readUpdates(`${run.repo}.${run.id}`, runsDir(), 1000)
    const questions = readAll(DIR).filter((q) => q.dossier === run.id && questionRepo(q, runs) === run.repo)
    const entries = withHtml(mergeTimeline({ updates, questions, names: PHASE_NAMES }))
    const groups = groupByPhase(entries, "Setup", false)
    const activity: unknown[] = []
    const now = nowPanel(entries, groups, { runner: run.runner?.state, waiting: questions.filter((q) => q.state === "open").map((q) => q.id), fallbackPhase: run.phase })
    return json({ run, entries, groups, activity, now, log, empty: entries.length === 0, planning: run.status === "planned" && updates.length > 0, phaseNames: PHASE_NAMES })
  }
  const dd = path.match(/^\/api\/dossier\/([\w.-]+)\/([\w.-]+)$/)
  if (req.method === "GET" && dd) {
    const run = await findRun(dd[1], dd[2])
    if (!run) return json({ error: "no such dossier" }, 404)
    try { return json({ run, html: renderMarkdown(readFileSync(run.file, "utf8").replace(/^---\n[\s\S]*?\n---\n?/, "")) }) } catch { return json({ error: "dossier unreadable" }, 404) }
  }
  if (req.method === "GET" && path === "/api/stub") {
    const repo = url.searchParams.get("repo") ?? "", branch = url.searchParams.get("branch") ?? ""
    const rs = (await getStubs(() => readAll(DIR))).repos.find((r) => r.repo === repo)
    const stub = rs?.stubs.find((s) => s.branch === branch)
    if (!rs || !stub) return json({ error: "no such stub" }, 404)
    const slug = branch.replace(/\//g, "-"), key = `${repo}.${slug}`
    const file = recordFile(rs.main, branch)
    let record = ""
    try { record = file ? readFileSync(file, "utf8") : "" } catch { /* unreadable */ }
    const questions = readAll(DIR).filter((q) => q.dossier === slug || q.dossier === branch || `${q.repo}.${q.dossier}` === key)
    const recordEntries = parseRecord(record, stub.cut)
    const upd = readUpdates(key, runsDir(), 1000)
    const extra = recordEntries.filter((e) => e.type === "boundary").map((e) => ({ ...e, text: "" }))
    const entries = withHtml(mergeTimeline({ updates: upd, questions, extra, unit: "Step" }))
    const groups = groupByPhase(entries, "Before the first step", false)
    const activity: unknown[] = []
    const now = nowPanel(entries, groups, { runner: "", waiting: questions.filter((q) => q.state === "open").map((q) => q.id), fallbackPhase: "" })
    return json({ repo, stub, key, entries, groups, activity, now, record: recordEntries, empty: entries.every((e) => e.type === "boundary"), hasRecord: !!file })
  }
  const rl = path.match(/^\/api\/runs\/([\w.-]+)\/([\w.-]+)\/log$/)
  if (req.method === "GET" && rl) {
    const t = runnerLog(rl[1], rl[2])
    return t === null ? json({ error: "no runner log" }, 404) : new Response(t, { headers: { "content-type": "text/plain; charset=utf-8" } })
  }
  const rc = path.match(/^\/api\/runs\/([\w.-]+)\/([\w.-]+)\/(start|resume|stop|fix-deferred)$/)
  if (rc) {
    if (req.method !== "POST") return json({ error: "method not allowed" }, 405)
    const origin = req.headers.get("origin")
    if (origin && origin !== `http://${host}`) return json({ error: "bad origin" }, 403)
    const [, repo, id, action] = rc
    const run = await findRun(repo, id)
    if (!run) return json({ error: "no such dossier" }, 404)
    const ctl = run.controls[action as "start" | "resume" | "stop" | "fix-deferred"]
    if (!ctl.show) return json({ error: `${action} is not available for this dossier` }, 409)
    if (!ctl.ok) return json({ error: ctl.reason }, 409)
    const root = action === "start" || action === "fix-deferred" ? run.main : (run.runner?.root || run.main)
    const key = `${repo}.${id}`
    if (inflightControl.has(key)) return json({ error: "another control is running for this dossier" }, 409)
    inflightControl.add(key)
    try {
      const r = await runRunner(action === "stop" ? ["stop", id, "--root", root] : action === "fix-deferred" ? [id, "--root", root, "--command", "fix-deferred"] : [id, "--root", root])
      emitChange("runs")
      return json({ ok: r.ok, output: r.output }, r.ok ? 200 : 500)
    } finally { inflightControl.delete(key) }
  }
  const dr = path.match(/^\/deferred\/([\w.-]+)\/([\w.-]+)$/)
  if (req.method === "GET" && dr) {
    try { return new Response(dossierPage(`${dr[2]} — deferred issues`, dr[1], readFileSync(join(runsDir(), `${dr[1]}.${dr[2]}.deferred.md`), "utf8")), { headers: { "content-type": "text/html; charset=utf-8" } }) }
    catch { return json({ error: "no deferred report for this dossier" }, 404) }
  }
  const ds = path.match(/^\/dossier\/([\w.-]+)\/([\w.-]+)$/)
  if (req.method === "GET" && ds) {
    const run = (await getRuns(() => readAll(DIR))).runs.find((r) => r.repo === ds[1] && r.id === ds[2])
    if (!run) return json({ error: "no such dossier" }, 404)
    try { return new Response(dossierPage(run.id, run.repo, readFileSync(run.file, "utf8")), { headers: { "content-type": "text/html; charset=utf-8" } }) } catch { return json({ error: "dossier unreadable" }, 404) }
  }
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
  // The first stubs and runs scan runs git and gh per repo and can pass Bun's 10 s idle default.
  const server = Bun.serve({ hostname: "127.0.0.1", port: requested !== undefined && requested !== "" ? Number(requested) : 7317, idleTimeout: 120, fetch: handle })
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
      emitChange("questions")
      clearTimeout(timer)
      timer = setTimeout(() => void scan(), 150)
    })
  } catch (e) {
    log(`fs.watch unavailable, rescan only: ${(e as Error).message}`)
  }
  try {
    mkdirSync(runsDir(), { recursive: true })
    let rt: ReturnType<typeof setTimeout> | undefined
    watch(runsDir(), () => { emitChange("runs"); clearTimeout(rt); rt = setTimeout(() => void scanRunThreads(), 300) })
  } catch (e) {
    log(`runs dir not watched: ${(e as Error).message}`)
  }
  setInterval(() => { void scan(); void scanRunThreads() }, 10_000)
}

if (import.meta.main) await main()
