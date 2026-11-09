// Owner-questions server. Run: bun --no-install shared/questions/server.ts
// Contract: references/owner-questions.md
import { appendFileSync, mkdirSync, openSync, readdirSync, statSync, watch, writeFileSync } from "node:fs"
import { AnsweredError, appendOwner, idForThread, readAll, readQuestion, readSidecar, storeDir, writeSidecar, type Line, type Question } from "./store.ts"
import { PAGE } from "./page.ts"
import { buildLog, getRuns, ownerRulingsFile, PHASE_NAMES, questionRepo, readDossier, undecidedDeferred, type Run } from "./runs.ts"
import { getStubs, openWorkHtml, recordFile, refreshAfterControl } from "./stubs.ts"
import { canIntegrate, checkRule, dossierPage, integrationPhase, integrationStopped, markIntegrating, readBlocks, readConversation, readIntegrate, readRunner, readUpdates, renderMarkdown, runnerActive, runsDir } from "./runstore.ts"
import { emitChange, subscribe } from "./events.ts"
import { groupByPhase, logEntries, mergeTimeline, nowPanel, parseRecord, splitSections } from "./timeline.ts"
import { runRunner, runTarget, runnerScript } from "./controls.ts"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { pendingSlack, readRunThread, runSlackEnabled, writeRunThread } from "./runslack.ts"
import { askAgent, type DmDeps } from "./dmagent.ts"
import { listChats, readChat, say } from "./pagechat.ts"
import { checkDisk, diskDeps, diskReading } from "./disk.ts"
import { api, apiGet, channelNameFor, keychain, pageLink, POINTER_TEXT, questionBlocks, runSocket, type OwnerAction } from "./slack.ts"
import { agentCounts, openerBlocks, statusBlocks, type StatusRun } from "./runstatus.ts"
import { readAccounts, usageDir } from "./accounts.ts"
import { mergeDetectorEnabled, nextMerges, readMergeSeen, writeMergeSeen, type MergedPr } from "./merges.ts"
import { sh } from "./runs.ts"
import { logBlock } from "../claude/permission-denied.ts"

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
  for (const e of es) if (e.type === "update" && (!e.auto || (e as any).event === "step") && e.text) e.html = renderMarkdown(e.text)
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
            const res = await api("chat.postMessage", botToken, { channel: await repoChannel(r.repo), text: m.text, blocks: openerBlocks(m.text, r.repo, r.id) })
            thread = { channel: res.channel ?? slackChannel, ts: res.ts }
          } else if (thread) await api("chat.postMessage", botToken, { channel: thread.channel, thread_ts: thread.ts, text: m.text })
        } catch (e) { log(`slack run thread: ${(e as Error).message}`) }
      }
      runSeen.set(key, upto)
      if (thread) writeRunThread(runsDir(), key, { ...thread, seen: upto })
    }
  } finally { scanningRuns = false }
}

// ---- Slack status on demand ----
/* `status` (or `status W-128`) in a repo channel lists that repo's running
   dossiers; in a run thread, that run; in the DM, every running dossier.
   The Status and Refresh buttons carry their scope and refresh in place. */
const channelRepo = new Map<string, string>()
async function repoOfChannel(channel: string, repos: string[]): Promise<string> {
  for (const [repo, id] of repoChannels) if (id === channel) return repo
  if (channelRepo.has(channel)) return channelRepo.get(channel)!
  let repo = ""
  try {
    const name = (await apiGet("conversations.info", botToken, { channel })).channel?.name ?? ""
    repo = repos.find((r) => [channelNameFor(r), `${channelNameFor(r)}-pipeline`.slice(0, 79)].includes(name)) ?? ""
  } catch { /* a DM, or no scope: no repo */ }
  channelRepo.set(channel, repo)
  return repo
}
function statusRun(r: Run): StatusRun {
  const key = `${r.repo}.${r.id}`
  const questions = r.questions.map((id) => ({ id, text: readQuestion(DIR, id)?.text ?? "" }))
  return {
    ...(r.runner?.waitingFor ? { waitingFor: r.runner.waitingFor } : {}),
    repo: r.repo, id: r.id, title: r.title, state: r.runner?.state ?? (r.running ? "running" : ""),
    phase: r.phase, phaseName: r.phaseName, account: r.runner?.account, started: r.runner?.started,
    resumesAt: r.runner?.resumesAt, context: r.session?.context || undefined, questions,
    updates: readUpdates(key, runsDir(), 3000),
    link: `http://127.0.0.1:${PORT}/repo/${encodeURIComponent(r.repo)}/${encodeURIComponent(r.id)}`,
  }
}
async function postStatus(a: Extract<OwnerAction, { type: "status" }>): Promise<void> {
  const runs = await allRuns()
  const repos = [...new Set(runs.map((r) => r.repo))]
  let repo = a.repo ?? ""
  let id = a.id ?? ""
  if (!id && a.threadTs) {
    const hit = runs.find((r) => { const t = readRunThread(runsDir(), `${r.repo}.${r.id}`); return t?.channel === a.channel && t.ts === a.threadTs })
    if (hit) { repo = hit.repo; id = hit.id }
  }
  if (!repo) repo = await repoOfChannel(a.channel, repos)
  const live = (r: Run) => !!r.runner && ["running", "waiting", "paused", "queued"].includes(r.runner.state)
  const picked = runs.filter((r) => (!repo || r.repo === repo) && (id ? r.id === id : live(r)))
  const { text, blocks } = statusBlocks(picked.map(statusRun), { repo: repo || undefined, id: id || undefined }, Date.now())
  if (a.refresh && a.ts) {
    // Refresh updates its own message; the Status button on a thread opener answers in that thread.
    await api("chat.update", botToken, { channel: a.channel, ts: a.ts, text, blocks })
    return
  }
  await api("chat.postMessage", botToken, { channel: a.channel, thread_ts: a.threadTs ?? a.ts, text, blocks })
}

// ---- Slack DM agent (dmagent.ts) ----
const DM_AGENT = process.env.ASD_DM_AGENT !== "0"
const dmDeps: DmDeps = {
  post: async (channel, threadTs, text) => (await api("chat.postMessage", botToken, { channel, thread_ts: threadTs, text })).ts,
  update: async (channel, ts, text) => { await api("chat.update", botToken, { channel, ts, text }) },
  configDir: () => (readAccounts([]).find((a) => a.next) ?? readAccounts([])[0])?.dir ?? process.env.CLAUDE_CONFIG_DIR ?? join(process.env.HOME || "", ".claude"),
  addDirs: async () => [...new Set((await allRuns()).map((r) => r.main).filter(Boolean))],
  log,
}
const { post: _p, update: _u, ...chatBase } = dmDeps

// ---- Slack inbound ----
async function onSlackAction(a: OwnerAction): Promise<void> {
  if (a.type === "status") {
    await postStatus(a)
    return
  }
  if (a.type === "ask") {
    void askAgent(a, dmDeps)
    return
  }
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
/** The page's version: an open tab reloads when the server it talks to serves a different page. */
const PAGE_ID = String(Bun.hash(PAGE))
const summaryInflight = new Set<string>()
let usageInflight = false

export async function handle(req: Request): Promise<Response> {
  const url = new URL(req.url)
  const host = req.headers.get("host") ?? ""
  if (!/^(127\.0\.0\.1|localhost)(:\d+)?$/.test(host)) return json({ error: "bad host" }, 403)
  const path = url.pathname
  const gr = path.match(/^\/repo\/([\w.-]+)\/graph$/)
  if (req.method === "GET" && gr) return new Response(null, { status: 302, headers: { location: `/repo/${gr[1]}` } })
  if (req.method === "GET" && (path === "/" || path === "/questions" || /^\/chat(\/[a-z0-9]+)?$/.test(path) || /^\/repo\/[\w.-]+(\/.*)?$/.test(path))) return html(PAGE)
  // The dashboard chat (pagechat.ts): the Slack DM agent, with this page as its channel.
  if (req.method === "GET" && path === "/api/chat") return json({ enabled: DM_AGENT, chats: listChats() })
  const cg = path.match(/^\/api\/chat\/([a-z0-9]{6,40})$/)
  if (req.method === "GET" && cg) {
    const c = readChat(cg[1])
    return c ? json({ ...c, messages: c.messages.map((m) => ({ ...m, html: renderMarkdown(m.text) })) }) : json({ error: "no such conversation" }, 404)
  }
  if (req.method === "POST" && path === "/api/chat") {
    if (!DM_AGENT) return json({ error: "the agent is off (ASD_DM_AGENT=0)" }, 503)
    const b = (await req.json().catch(() => ({}))) as { text?: unknown; id?: unknown }
    const text = typeof b.text === "string" ? b.text.trim() : ""
    if (!text) return json({ error: "empty message" }, 400)
    if (b.id !== undefined && (typeof b.id !== "string" || !/^[a-z0-9]{6,40}$/.test(b.id))) return json({ error: "bad conversation id" }, 400)
    try {
      const r = await say(text.slice(0, 20_000), b.id as string | undefined, chatBase, () => emitChange("chat"))
      return json({ ok: true, id: r.id })
    } catch (e) { return json({ error: (e as Error).message }, 404) }
  }
  if (req.method === "GET" && path === "/api/events") return events(req)
  if (req.method === "GET" && path === "/api/runs") {
    // Live runs carry their agent counts (this stage); the accounts carry their windows and which runs use them.
    const v = await getRuns(() => readAll(DIR))
    const openQs = new Set(readAll(DIR).filter((q) => q.state === "open").map((q) => q.id))
    const runs = v.runs.map((r) => (runnerActive(r.runner) ? { ...r, agents: agentCounts(readUpdates(`${r.repo}.${r.id}`, runsDir(), 3000)), blocked: readBlocks(`${r.repo}.${r.id}`).length } : r))
      .map((r) => { const m = mergeState(`${r.repo}.${r.id}`); return m ? { ...r, merge: m } : r })
      .map((r) => ({ ...r, deferredUndecided: undecidedDeferred(r.deferred.items, r.runner?.command === "fix-deferred" && runnerActive(r.runner), openQs) }))
    const plans = planRuns(runs)
    const accounts = readAccounts([...runs.filter((r) => runnerActive(r.runner)).map((r) => ({ key: r.runKey || `${r.repo}.${r.id}`, account: r.runner?.account })), ...plans.map((p) => ({ key: p.key, account: p.account }))])
    return json({ ...v, runs, plans, accounts, disk: diskReading(), usageBusy: usageInflight, slack: slackOn(), pageId: PAGE_ID })
  }
  if (path === "/api/accounts/refresh") {
    if (req.method !== "POST") return json({ error: "method not allowed" }, 405)
    const origin = req.headers.get("origin")
    if (origin && origin !== `http://${host}`) return json({ error: "bad origin" }, 403)
    if (usageInflight) return json({ error: "already checking" }, 409)
    usageInflight = true
    // One tiny haiku call per account reads its windows; the page refreshes when the files land.
    void runRunner(["usage"], 5 * 60_000).finally(() => { usageInflight = false; emitChange("runs") })
    emitChange("runs")
    return json({ ok: true, output: "checking the accounts" })
  }
  if (req.method === "GET" && path === "/api/stubs") {
    const view = await getStubs(() => readAll(DIR))
    return json({ ...view, repos: view.repos.map((r) => ({ ...r, stubs: r.stubs.map((s) => {
      const key = `${r.repo}.${s.branch.replace(/\//g, "-")}`
      const integrate = readIntegrate(key), runner = readRunner(key)
      const stopped = s.state === "integrating" && integrationStopped(integrate, runner)
      return { ...s, canIntegrate: canIntegrate(s.state, integrate, runner), stopped, stoppedWhy: stopped ? integrate?.error ?? "" : "", ...(s.state === "integrating" ? { phase: integrationPhase(integrate, runner) } : {}) }
    }) })) })
  }
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
    try { log = logEntries(buildLog(readDossier(run.file))) } catch { /* unreadable */ }
    const updates = readUpdates(run.runKey || `${run.repo}.${run.id}`, runsDir(), 1000)
    const questions = readAll(DIR).filter((q) => q.dossier === run.id && questionRepo(q, runs) === run.repo)
    const entries = withHtml(mergeTimeline({ updates, questions, names: PHASE_NAMES }))
    // One tab per run section: Implement dossier (the /work-on phases), then each later command (its own steps).
    const sections = splitSections(entries).map((x) => ({ cmd: x.cmd, label: x.label, lastTs: x.lastTs, groups: groupByPhase(x.entries, x.cmd ? "Started" : "Setup", false) }))
    const groups = sections[0].groups
    const activity: unknown[] = []
    // The Now panel follows the section the run is in now (the newest one).
    const latest = sections.reduce((a, b) => (b.lastTs > a.lastTs ? b : a), sections[0])
    const now = nowPanel(entries, latest.groups, { runner: run.runner?.state, waiting: questions.filter((q) => q.state === "open").map((q) => q.id), fallbackPhase: run.phase === "plan" ? "" : run.phase })
    let html = ""
    try { html = renderMarkdown(readDossier(run.file).replace(/^---\n[\s\S]*?\n---\n?/, "")) } catch { /* unreadable */ }
    let summary: { html: string; ts: string } | null = null
    try {
      const sp = join(runsDir(), `${run.repo}.${run.id}.summary.md`)
      summary = { html: renderMarkdown(readFileSync(sp, "utf8")), ts: new Date(statSync(sp).mtimeMs).toISOString() }
    } catch { /* none yet */ }
    const c = readConversation(`${run.repo}.${run.id}`, run.runner?.state === "done")
    const conv = { ...c, result: c.result ? { ...c.result, html: renderMarkdown(c.result.text) } : null, chat: c.chat.map((l) => ({ ...l, html: renderMarkdown(l.text) })), canChat: !!c.result && !runnerActive(run.runner) }
    const blocks = readBlocks(`${run.repo}.${run.id}`)
    const openQ = questions.filter((q) => q.state === "open").sort((a, b) => b.ts.localeCompare(a.ts))[0]?.id ?? ""
    return json({ run, html, entries, groups, sections, activeSection: latest.cmd, activity, now, log, conv, summary, blocks, blockQuestion: openQ, summaryBusy: summaryInflight.has(`${run.repo}.${run.id}`), empty: entries.length === 0, planning: run.status === "planned" && updates.length > 0, phaseNames: PHASE_NAMES })
  }
  const dd = path.match(/^\/api\/dossier\/([\w.-]+)\/([\w.-]+)$/)
  if (req.method === "GET" && dd) {
    const run = await findRun(dd[1], dd[2])
    if (!run) return json({ error: "no such dossier" }, 404)
    try { return json({ run, html: renderMarkdown(readDossier(run.file).replace(/^---\n[\s\S]*?\n---\n?/, "")) }) } catch { return json({ error: "dossier unreadable" }, 404) }
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
    const runner = readRunner(key)
    const now = nowPanel(entries, groups, { runner: runner?.state ?? "", waiting: questions.filter((q) => q.state === "open").map((q) => q.id), fallbackPhase: "" })
    const integrate = readIntegrate(key)
    let land: any = null
    try { land = JSON.parse(readFileSync(join(runsDir(), `${key}.land.json`), "utf8")) } catch { /* the scripted merge has not looked at it */ }
    return json({ repo, stub, key, entries, groups, activity, now, runner, integrate, land, record: recordEntries, empty: entries.every((e) => e.type === "boundary"), hasRecord: !!file })
  }
  // Integrate a stub: its finished dossiers through the merge queue (FIFO), then /stub on the runner; and Stop/Resume of
  // that /stub run. POST {repo, branch, action: integrate|stop|resume}.
  if (req.method === "POST" && path === "/api/stub/control") {
    const origin = req.headers.get("origin")
    if (origin && origin !== `http://${host}`) return json({ error: "bad origin" }, 403)
    const b = await req.json().catch(() => ({})) as { repo?: string; branch?: string; action?: string }
    const rs = (await getStubs(() => readAll(DIR))).repos.find((r) => r.repo === b.repo)
    const stub = rs?.stubs.find((x) => x.branch === b.branch)
    if (!rs || !stub || !/^(stub|release)\/[\w.-]+$/.test(stub.branch)) return json({ error: "no such stub" }, 404)
    const slug = stub.branch.replace(/\//g, "-")
    if (b.action === "integrate") {
      const fd = openSync(join(runsDir(), `${rs.repo}.${slug}.integrate.log`), "a")
      const proc = Bun.spawn(["python3", mergeScript(), "--integrate", stub.branch, "--root", rs.main], { stdout: fd, stderr: fd, stdin: "ignore" })
      proc.unref()
      markIntegrating(`${rs.repo}.${slug}`, { pid: proc.pid, root: rs.main, branch: stub.branch })   // so the button goes with this response
      emitChange("stubs")
      return json({ ok: true })
    }
    if (b.action === "stop" || b.action === "resume") {
      const r = await runRunner(b.action === "stop" ? ["stop", slug, "--root", rs.main] : [slug, "--root", rs.main, "--command", "stub", "--items", stub.branch])
      emitChange("stubs")
      return json({ ok: r.ok, output: r.output }, r.ok ? 200 : 409)
    }
    return json({ error: "action is integrate, stop or resume" }, 400)
  }
  const rl = path.match(/^\/api\/runs\/([\w.-]+)\/([\w.-]+)\/log$/)
  if (req.method === "GET" && rl) {
    const t = runnerLog(rl[1], rl[2])
    return t === null ? json({ error: "no runner log" }, 404) : new Response(t, { headers: { "content-type": "text/plain; charset=utf-8" } })
  }
  const bl = path.match(/^\/api\/runs\/([\w.-]+)\/([\w.-]+)\/blocks\/([\w.-]+)$/)
  if (bl) {
    if (req.method !== "POST") return json({ error: "method not allowed" }, 405)
    const origin = req.headers.get("origin")
    if (origin && origin !== `http://${host}`) return json({ error: "bad origin" }, 403)
    const run = await findRun(bl[1], bl[2])
    if (!run) return json({ error: "no such dossier" }, 404)
    const key = `${run.repo}.${run.id}`
    const b = readBlocks(key, runsDir(), 50).find((x) => x.id === bl[3])
    if (!b) return json({ error: "no such block, or it was ruled on already" }, 404)
    const body = await req.json().catch(() => ({})) as { action?: unknown; rule?: unknown; restart?: unknown }
    const action = String(body.action ?? "")
    if (!["once", "always", "deny"].includes(action)) return json({ error: "action is once, always or deny" }, 400)
    // The agent hears the ruling through its open question: an option-style answer, which the classifier reads as the
    // owner's own words naming the exact action.
    const rs = await allRuns()
    const qs = readAll(DIR).filter((q) => q.dossier === run.id && questionRepo(q, rs) === run.repo && q.state === "open").sort((a, c) => c.ts.localeCompare(a.ts))
    const q = qs[0]
    const what = b.tool === "Bash" ? `this exact command: \`${b.target}\`` : `${b.tool} on \`${b.target}\``
    let rule = ""
    if (action === "always") {
      rule = typeof body.rule === "string" && body.rule.trim() ? body.rule.trim() : `${b.tool}(${b.target})`
      const bad = checkRule(rule)
      if (bad) return json({ error: bad }, 400)
      const f = join(run.main, ".claude", "settings.local.json")
      let cfg: any = {}
      try { cfg = JSON.parse(readFileSync(f, "utf8")) } catch { cfg = {} }
      cfg.permissions = cfg.permissions ?? {}
      const allow: string[] = Array.isArray(cfg.permissions.allow) ? cfg.permissions.allow : []
      if (!allow.includes(rule)) allow.push(rule)
      cfg.permissions.allow = allow
      mkdirSync(join(run.main, ".claude"), { recursive: true })
      writeFileSync(f, JSON.stringify(cfg, null, 2) + "\n")
      // A rule only reaches a new process: ask the runner to restart the stage when this ruling arrives (safe point).
      if (runnerActive(run.runner) && body.restart !== false) writeFileSync(join(runsDir(), `${key}.restart`), rule + "\n")
    }
    if (!q && action !== "always") return json({ error: "the agent has not asked about it yet; it asks when it cannot go on, or use Always allow" }, 409)
    const text = action === "deny"
      ? `Denied: do not run ${what}. Carry on without it, and say in the pull request what that leaves unverified.`
      : `Approved${action === "always" ? ", and saved as a standing rule for this repo (" + rule + ")" : ", once"}: you may run ${what}. The safety check blocked it as: ${b.reason || "no reason given"}. I accept that risk for this ${action === "always" ? "command and the ones the rule covers" : "one command"}.`
    if (q) appendOwner(DIR, q.id, { kind: "answer", text, option: "", via: "page" })
    logBlock({ ts: new Date().toISOString().replace(/\.\d+Z$/, "Z"), event: "block-ruling", run: key, tool: b.tool, target: b.target, tool_use_id: b.id, scope: action, rule: rule || undefined })
    appendFileSync(join(runsDir(), `${key}.jsonl`), JSON.stringify({ ts: new Date().toISOString().replace(/\.\d+Z$/, "Z"), kind: "update", auto: true, event: "block-ruling", tool_use_id: b.id, target: b.target, scope: action, rule, text: `block ruled: ${action}${rule ? " (" + rule + ")" : ""}` }) + "\n")
    emitChange("runs"); emitChange("questions")
    const restarting = action === "always" && runnerActive(run.runner) && body.restart !== false
    return json({ ok: true, output: (action === "always" ? `saved ${rule} to ${run.repo}'s .claude/settings.local.json; ${restarting ? (q ? "the stage restarts now with your answer, so the rule applies" : "the stage restarts with the rule once the agent asks and you answer") : "it applies from the next stage"}` : `sent to the agent`) + (q ? ` · answered ${q.id}` : " · no open question to answer") })
  }
  const sm = path.match(/^\/api\/runs\/([\w.-]+)\/([\w.-]+)\/summary$/)
  if (sm) {
    if (req.method !== "POST") return json({ error: "method not allowed" }, 405)
    const origin = req.headers.get("origin")
    if (origin && origin !== `http://${host}`) return json({ error: "bad origin" }, 403)
    const run = await findRun(sm[1], sm[2])
    if (!run) return json({ error: "no such dossier" }, 404)
    if (!["done", "pr", "review"].includes(run.status)) return json({ error: "nothing is built yet" }, 409)
    if (runnerActive(run.runner)) return json({ error: "the run is still going; it writes the summary when it is done" }, 409)
    const key = `${run.repo}.${run.id}`
    if (summaryInflight.has(key)) return json({ error: "the summary is already being written" }, 409)
    summaryInflight.add(key)
    // A minute or two of model time: run it in the background, the page refreshes when the file lands.
    void runRunner(["summary", run.id, "--root", run.runner?.root || run.main], 15 * 60_000).finally(() => { summaryInflight.delete(key); emitChange("runs") })
    emitChange("runs")
    return json({ ok: true, output: "writing the product summary" })
  }
  const ch = path.match(/^\/api\/runs\/([\w.-]+)\/([\w.-]+)\/chat$/)
  if (ch) {
    if (req.method !== "POST") return json({ error: "method not allowed" }, 405)
    const origin = req.headers.get("origin")
    if (origin && origin !== `http://${host}`) return json({ error: "bad origin" }, 403)
    const run = await findRun(ch[1], ch[2])
    if (!run) return json({ error: "no such dossier" }, 404)
    // A live run gets the message through its runner (run_dossier.py chat queues it); a finished one resumes its session.
    const body = await req.json().catch(() => ({})) as { text?: unknown }
    const text = typeof body.text === "string" ? body.text.trim() : ""
    if (!text || text.length > 8000) return json({ error: "write a message (at most 8000 characters)" }, 400)
    const r = await runRunner(["chat", run.id, "--root", run.runner?.root || run.main, "--text", text])
    emitChange("runs")
    return json({ ok: r.ok, output: r.output }, r.ok ? 200 : 409)
  }
  // Merge: all of a dossier's open PRs into its stub as one GitHub stack (scripts/merge_dossier.py, no agent), then
  // the /after-merge cleanup. GET is the dry run the confirm button shows; POST starts it in the background.
  const mg = path.match(/^\/api\/runs\/([\w.-]+)\/([\w.-]+)\/merge$/)
  if (mg && (req.method === "GET" || req.method === "POST")) {
    const origin = req.headers.get("origin")
    if (req.method === "POST" && origin && origin !== `http://${host}`) return json({ error: "bad origin" }, 403)
    const run = await findRun(mg[1], mg[2])
    if (!run) return json({ error: "no such dossier" }, 404)
    const m = mergeState(`${run.repo}.${run.id}`)
    const args = ["python3", mergeScript(), run.id, "--root", run.main]
    const body = req.method === "POST" ? await req.json().catch(() => ({})) as { cancel?: unknown } : {}
    // Cancel: leave the queue (refused for the dossier being merged now).
    if (req.method === "POST" && body.cancel === true) {
      const r = Bun.spawnSync([...args, "--unqueue"], { stdout: "pipe", stderr: "pipe" })
      emitChange("runs")
      return json({ ok: r.exitCode === 0, text: r.stdout.toString().trim().replace(/^merge_dossier:\s*/, "") }, r.exitCode === 0 ? 200 : 409)
    }
    if (req.method === "POST" && m && ["queued", "waiting", "resolving", "merging", "cleanup"].includes(m.state)) return json({ error: `already in the merge queue (${m.state})` }, 409)
    if (req.method === "GET") {
      const r = Bun.spawnSync([...args, "--dry-run"], { stdout: "pipe", stderr: "pipe" })
      const line = (r.stdout.toString() || r.stderr.toString()).trim().split("\n").pop()?.replace(/^merge_dossier:\s*/, "") ?? ""
      return json({ ok: r.exitCode === 0, text: line })
    }
    // Merge: join the repository's merge queue (first in, first out); its worker resolves conflicts through the
    // dossier's agent, merges by script and runs the cleanup, one dossier at a time.
    const r = Bun.spawnSync([...args, "--enqueue"], { stdout: "pipe", stderr: "pipe" })
    emitChange("runs")
    return json({ ok: r.exitCode === 0, text: (r.stdout.toString() || r.stderr.toString()).trim().replace(/^merge_dossier:\s*/, "") }, r.exitCode === 0 ? 200 : 500)
  }
  // Non-code PRs into development (plan/*, docs/*): the list with why each can or cannot merge, and "Merge all"
  // (scripts/merge_docs_prs.py, in the background; no agent merges). The list is cached a minute: it asks gh per PR.
  const dp = path.match(/^\/api\/repo\/([\w.-]+)\/doc-prs(\/merge)?$/)
  if (dp) {
    const repo = dp[1]
    const main = (await allRuns()).find((r) => r.repo === repo)?.main
    if (!main) return json({ error: "no such repo" }, 404)
    const stateFile = join(runsDir(), `${repo}.docmerge.json`)
    let st: any = {}
    try { st = JSON.parse(readFileSync(stateFile, "utf8")) } catch { /* never merged */ }
    const merging = !!st.pid && (() => { try { process.kill(Number(st.pid), 0); return true } catch { return false } })()
    let log = ""
    try { log = readFileSync(join(runsDir(), `${repo}.docmerge.log`), "utf8").trim().split("\n").slice(-12).join("\n") } catch { /* none */ }
    if (req.method === "POST" && dp[2]) {
      const origin = req.headers.get("origin")
      if (origin && origin !== `http://${host}`) return json({ error: "bad origin" }, 403)
      if (merging) return json({ error: "already merging" }, 409)
      const fd = openSync(join(runsDir(), `${repo}.docmerge.log`), "w")
      const proc = Bun.spawn(["python3", join(import.meta.dir, "..", "..", "scripts", "merge_docs_prs.py"), "merge", "--root", main], { stdout: fd, stderr: fd, stdin: "ignore" })
      writeFileSync(stateFile, JSON.stringify({ pid: proc.pid, started: new Date().toISOString() }))
      proc.exited.then(() => { docPrsCache.delete(repo); emitChange("runs") })
      return json({ ok: true })
    }
    if (req.method !== "GET") return json({ error: "method not allowed" }, 405)
    const c = docPrsCache.get(repo)
    let prs: any = c && Date.now() - c.at < 60_000 && !url.searchParams.has("fresh") ? c.v : null
    if (!prs) {
      const r = Bun.spawnSync(["python3", join(import.meta.dir, "..", "..", "scripts", "merge_docs_prs.py"), "list", "--root", main], { stdout: "pipe", stderr: "pipe" })
      try { prs = JSON.parse(r.stdout.toString()) } catch { prs = { error: (r.stderr.toString() || "list failed").slice(0, 300) } }
      docPrsCache.set(repo, { at: Date.now(), v: prs })
    }
    return json({ ...prs, merging, log })
  }
  // Leave open / Reopen: the owner's own ruling on an open deferred item, kept beside the run (withOwnerRulings).
  const lo = path.match(/^\/api\/runs\/([\w.-]+)\/([\w.-]+)\/deferred-keep$/)
  if (lo && req.method === "POST") {
    const origin = req.headers.get("origin")
    if (origin && origin !== `http://${host}`) return json({ error: "bad origin" }, 403)
    const run = await findRun(lo[1], lo[2])
    if (!run) return json({ error: "no such dossier" }, 404)
    const body = await req.json().catch(() => ({})) as { item?: unknown; keepOpen?: unknown }
    const item = typeof body.item === "string" ? body.item : ""
    const it = run.deferred.items.find((i) => i.id === item)
    if (!it || it.state !== "open") return json({ error: "only an open item can be left open or reopened" }, 409)
    const f = ownerRulingsFile(`${run.repo}.${run.id}`)
    let o: Record<string, unknown> = {}
    try { o = JSON.parse(readFileSync(f, "utf8")) } catch { /* first ruling */ }
    o[item] = { keepOpen: body.keepOpen !== false, ts: new Date().toISOString() }
    writeFileSync(f, JSON.stringify(o, null, 1))
    appendFileSync(join(runsDir(), `${run.repo}.${run.id}.jsonl`), JSON.stringify({ ts: new Date().toISOString().replace(/\.\d+Z$/, "Z"), kind: "update", text: `${item} ${body.keepOpen !== false ? "left open" : "reopened"} by the owner on the dashboard` }) + "\n")
    emitChange("runs")
    return json({ ok: true })
  }
  // Plan it: one item ruled to its own dossier gets a /plan run of its own (`<repo>.<ID>.plan-<D>`), beside any run of the source.
  const pi = path.match(/^\/api\/runs\/([\w.-]+)\/([\w.-]+)\/plan-item$/)
  if (pi && req.method === "POST") {
    const origin = req.headers.get("origin")
    if (origin && origin !== `http://${host}`) return json({ error: "bad origin" }, 403)
    const run = await findRun(pi[1], pi[2])
    if (!run) return json({ error: "no such dossier" }, 404)
    const body = await req.json().catch(() => ({})) as { item?: unknown }
    const item = typeof body.item === "string" ? body.item : ""
    const it = run.deferred.items.find((i) => i.id === item)
    if (!it || it.state !== "dossier" || it.dossier) return json({ error: "only an item ruled to its own dossier, not planned yet" }, 409)
    if (it.planning && ["running", "waiting", "paused", "queued"].includes(it.planning)) return json({ error: "it is being planned" }, 409)
    const r = await runRunner([run.id, "--root", run.main, "--command", "plan", "--items", item])
    emitChange("runs")
    return json({ ok: r.ok, output: r.output }, r.ok ? 200 : 409)
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
    // "Fix selected": the D-ids the owner ticked, each an item still to tackle; none = every open item.
    let items: string[] = []
    if (action === "fix-deferred") {
      const body = await req.json().catch(() => ({})) as { items?: unknown }
      items = Array.isArray(body.items) ? [...new Set(body.items.filter((x): x is string => typeof x === "string"))] : []
      const ok = new Set(run.deferred.items.filter((i) => i.state === "open").map((i) => i.id))
      const bad = items.filter((i) => !ok.has(i))
      if (bad.length) return json({ error: `not an item to fix: ${bad.join(", ")}` }, 400)
      // "Fix all deferred" means every open item except the ones the owner ruled to leave open: name them explicitly,
      // so the run never sweeps a kept-open item in. (Ticking one by hand still fixes it.)
      if (!items.length) {
        items = run.deferred.items.filter((i) => i.state === "open" && !i.keptOpen).map((i) => i.id)
        if (!items.length) return json({ error: "nothing to fix: every open item was ruled to stay open" }, 409)
      }
    }
    const key = `${repo}.${id}`
    if (inflightControl.has(key)) return json({ error: "another control is running for this dossier" }, 409)
    inflightControl.add(key)
    try {
      // Start runs the dossier's next flow (plan or work-on); Resume runs the flow that stopped, with its items.
      const flow = action === "start" ? [`--command`, ctl.command || "work-on"]
        : action === "resume" ? ["--command", ctl.command || "work-on", ...(run.runner?.items ? ["--items", run.runner.items] : [])] : []
      const target = runTarget(repo, id, run.runKey, action)
      const r = await runRunner(action === "stop" ? ["stop", target.stop, "--root", root] : action === "fix-deferred" ? [id, "--root", root, "--command", "fix-deferred", ...(items.length ? ["--items", items.join(",")] : [])] : [target.dossier, "--root", root, ...flow])
      if (r.ok) { refreshAfterControl(repo); void getStubs(() => readAll(DIR)) }
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
    try { return new Response(dossierPage(run.id, run.repo, readDossier(run.file)), { headers: { "content-type": "text/html; charset=utf-8" } }) } catch { return json({ error: "dossier unreadable" }, 404) }
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

/**
 * Live /plan runs of a deferred item whose dossier is not written yet (`<repo>.<src>.plan-<D-n>.runner.json` with no
 * dossier carrying its key): Running now shows them, linked to the source dossier, until the new dossier takes over.
 */
function planRuns(runs: Run[]): { key: string; repo: string; src: string; item: string; title: string; state: string; started: string; account?: string; stage: string; last: string; lastTs: string }[] {
  const taken = new Set(runs.map((r) => r.runKey).filter(Boolean))
  let names: string[] = []
  try { names = readdirSync(runsDir()).filter((n) => /\.plan-D-\d+\.runner\.json$/.test(n)) } catch { return [] }
  const out = []
  for (const n of names) {
    const key = n.replace(/\.runner\.json$/, "")
    if (taken.has(key)) continue
    const m = key.match(/^(.+)\.([A-Z][A-Z0-9]*-\d+)\.plan-(D-\d+)$/)
    if (!m) continue
    const rn = readRunner(key)
    if (!rn || !runnerActive(rn)) continue
    const src = runs.find((r) => r.repo === m[1] && r.id === m[2])
    const it = src?.deferred.items.find((i) => i.id === m[3])
    const ups = readUpdates(key, runsDir(), 50)
    const note = ups.find((u) => !u.auto && u.kind === "update") ?? ups[0]
    out.push({ key, repo: m[1], src: m[2], item: m[3], title: it?.title || `${m[2]} ${m[3]}`, state: rn.state, started: rn.started, account: rn.account, stage: rn.stage, last: note?.text ?? "", lastTs: note?.ts ?? "" })
  }
  return out
}

/** The Merge button's state, `<key>.merge.json` from scripts/merge_dossier.py; a "merging" whose process is gone is failed. */
function mergeState(key: string): { state: string; prs: number[]; base: string; error: string; ts: string; position?: number; note?: string } | null {
  const alive = (pid: unknown) => { try { process.kill(Number(pid), 0); return true } catch { return false } }
  try {
    const j = JSON.parse(readFileSync(join(runsDir(), `${key}.merge.json`), "utf8"))
    if (["waiting", "resolving", "merging", "cleanup"].includes(j.state) && j.pid && !alive(j.pid)) return { ...j, state: "failed", error: "the merge queue stopped before this dossier was done; press Merge again" }
    if (j.state === "queued") {
      const repo = key.replace(/\.[A-Z][A-Z0-9]*-\d+$/, "")
      let q: any = {}
      try { q = JSON.parse(readFileSync(join(runsDir(), `${repo}.merge-queue.json`), "utf8")) } catch { /* none */ }
      if (!(q.worker && alive(q.worker))) return { ...j, state: "failed", error: "the merge queue stopped; press Merge again" }
    }
    return j
  } catch { return null }
}
const docPrsCache = new Map<string, { at: number; v: any }>()
const mergeScript = (): string => process.env.ASD_MERGE_BIN || join(import.meta.dir, "..", "..", "scripts", "merge_dossier.py")

/**
 * The merge detector: every 2 minutes, the PRs merged into each open stub (GitHub). A dossier's PR that merged since
 * the last look starts `/after-merge` for that dossier on the runner (sonnet), once; a dossier whose runner is live
 * waits for the next poll. The first look only records what is already merged. `ASD_MERGE_DETECTOR=0` turns it off.
 */
let mergeInflight = false
async function checkMerges(): Promise<void> {
  if (mergeInflight || !mergeDetectorEnabled() || process.env.ASD_RUNS_NO_GH === "1") return
  mergeInflight = true
  try {
    const st = await getStubs(() => readAll(DIR))
    const runs = (await getRuns(() => readAll(DIR))).runs
    for (const rr of st.repos) {
      if (!rr.open.length) continue
      // Only a main checkout: a scan that took a worktree for a repo (its `.git` is a file) must not baseline or start runs.
      try { if (!statSync(join(rr.main, ".git")).isDirectory()) continue } catch { continue }
      const found: MergedPr[] = []
      let ok = true
      for (const base of rr.open) {
        const out = await sh(["gh", "pr", "list", "--base", base, "--state", "merged", "--limit", "50", "--json", "number,headRefName,mergedAt"], rr.main, 15_000)
        let l: any[] | null = null
        try { l = JSON.parse(out) } catch { /* gh failed */ }
        if (!Array.isArray(l)) { ok = false; break }
        for (const x of l) found.push({ number: Number(x.number), headRefName: String(x.headRefName ?? ""), mergedAt: String(x.mergedAt ?? ""), base })
      }
      if (!ok) continue   // a failed gh call never counts as "nothing merged": the baseline would miss it
      const mine = runs.filter((r) => r.repo === rr.repo)
      const dossiers = mine.map((r) => ({ id: r.id, pr: r.pr ? String(r.pr.number) : undefined }))
      const busy = (id: string) => runnerActive(mine.find((r) => r.id === id)?.runner ?? null)
      const now = new Date().toISOString()
      const next = nextMerges(readMergeSeen(rr.repo), found, dossiers, busy, now)
      writeMergeSeen(rr.repo, next.seen)
      for (const s of next.start) {
        const r = await runRunner([s.id, "--root", rr.main, "--command", "after-merge", "--items", s.prs.join(","), "--model", "sonnet"])
        log(`merge detector: ${rr.repo} ${s.id} PR ${s.prs.join(",")} merged — after-merge ${r.ok ? "started" : "not started: " + r.output}`)
        if (!r.ok) { const seen = readMergeSeen(rr.repo) ?? {}; for (const n of s.prs) if (seen[String(n)]) seen[String(n)].state = "pending"; writeMergeSeen(rr.repo, seen) }
      }
      if (next.start.length) emitChange("runs")
    }
  } catch (e) {
    log(`merge detector: ${(e as Error).message}`)
  } finally { mergeInflight = false }
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
      void runSocket(appToken, { onAction: onSlackAction, lookup: (c, t) => idForThread(DIR, c, t), ownerUser: owner, dmAgent: DM_AGENT, log })
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
    watch(runsDir(), (_e, name) => { emitChange("runs"); if (/\.(integrate|land|runner)\.json$/.test(String(name ?? "")) && /\.(stub|release)-/.test(String(name ?? ""))) emitChange("stubs"); clearTimeout(rt); rt = setTimeout(() => void scanRunThreads(), 300) })
  } catch (e) {
    log(`runs dir not watched: ${(e as Error).message}`)
  }
  try {
    mkdirSync(usageDir(), { recursive: true })
    watch(usageDir(), () => emitChange("runs"))
  } catch { /* the 10 s rescan still picks the readings up */ }
  setInterval(() => { void scan(); void scanRunThreads() }, 10_000)
  setTimeout(() => void checkMerges(), 20_000)
  setInterval(() => void checkMerges(), 120_000)
  // A merge queue outlives its worker (a logout, a reboot, a crash): pick it up again where it was.
  // A run killed rather than stopped (its record says working, its process is gone) goes on from its checkpoints too.
  const reviveQueues = () => { try {
    Bun.spawn(["python3", mergeScript(), "--revive"], { stdout: "ignore", stderr: "ignore", stdin: "ignore" }).unref()
    Bun.spawn(["python3", runnerScript(), "revive"], { stdout: "ignore", stderr: "ignore", stdin: "ignore" }).unref()
  } catch { /* the next tick tries again */ } }
  setTimeout(reviveQueues, 5_000)
  setInterval(reviveQueues, 120_000)
  // The disk watch: reclaim unused build output before the disk fills, and say so once when it drops below the floor.
  if (process.env.ASD_DISK_WATCH !== "0") {
    const dd = diskDeps((t, b) => notify(t, "disk", b), () => emitChange("runs"))
    setTimeout(() => void checkDisk(dd), 3_000)
    setInterval(() => void checkDisk(dd), Number(process.env.ASD_DISK_EVERY_MS) || 300_000)
  }
}

if (import.meta.main) await main()
