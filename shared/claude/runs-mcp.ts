// The `runs` MCP server: what the local dashboard shows, and the actions its buttons take, for any agent.
// A thin client of the dashboard's HTTP API (shared/questions/server.ts, http://127.0.0.1:7317, ASD_QUESTIONS_PORT):
// no state and no rules of its own, so the page, Slack and this server always agree. Registered in the plugin's
// .mcp.json as `runs`; Claude Code names the tools mcp__plugin_agentic-software-development_runs__<tool>.
// Contract: references/owner-questions.md (Dashboard, "The runs MCP server").

const SERVER = { name: "agentic-software-development-runs", version: "1.0.0" }
const SUPPORTED = ["2025-06-18", "2025-03-26", "2024-11-05"]
type Rpc = { jsonrpc?: string; id?: string | number | null; method?: string; params?: any }
type Param = { name: string; type: "string" | "array"; description: string; optional?: boolean; enum?: string[] }
type Tool = { name: string; description: string; params: Param[]; run: (a: Record<string, any>) => Promise<string> }

const port = (): string => process.env.ASD_QUESTIONS_PORT || "7317"
const base = (): string => `http://127.0.0.1:${port()}`
const enc = encodeURIComponent
const clip = (s: unknown, n: number): string => { const t = String(s ?? ""); return t.length > n ? t.slice(0, n - 1) + "…" : t }
const text = (s: string, isError = false) => ({ content: [{ type: "text", text: s }], ...(isError ? { isError: true } : {}) })

class Down extends Error {}
async function api(path: string, init?: { method?: string; body?: unknown }): Promise<{ status: number; body: any }> {
  let r: Response
  try {
    r = await fetch(base() + path, {
      method: init?.method ?? "GET",
      // The dashboard refuses a POST from another origin; this server is the page's own.
      headers: init?.method === "POST" ? { "content-type": "application/json", origin: base() } : {},
      body: init?.method === "POST" ? JSON.stringify(init.body ?? {}) : undefined,
      signal: AbortSignal.timeout(Number(process.env.ASD_RUNS_MCP_TIMEOUT_MS || 120_000)),
    })
  } catch (e) {
    throw new Down(`the dashboard is not reachable at ${base()} (${(e as Error).message}). Start it with: launchctl kickstart -k gui/$(id -u)/dev.tristan.asd-questions, or bun shared/questions/server.ts`)
  }
  const raw = await r.text()
  let body: any = raw
  try { body = JSON.parse(raw) } catch { /* plain text */ }
  return { status: r.status, body }
}
const out = (v: unknown): string => JSON.stringify(v, null, 1)
async function act(path: string, body: unknown = {}, method = "POST"): Promise<string> {
  const r = await api(path, { method, body })
  const ok = r.status < 300 && !(r.body && r.body.ok === false)
  return (ok ? "ok" : `refused (HTTP ${r.status})`) + ": " + clip(typeof r.body === "string" ? r.body : out(r.body), 4000)
}

const LIVE = ["running", "waiting", "paused", "queued"]
/** One run as one line: `repo ID · state · command · account · phase · PR · questions — title — last note`. */
function line(r: any): string {
  const b = brief(r)
  const quiet = r.runner?.state === "queued"
  return [`${b.repo} ${b.id}`, b.run || b.status, b.phase && `phase ${b.phase}`, b.pr && `PR ${b.pr}`, b.openQuestions && `${b.openQuestions} open question(s)`,
    b.merge && `merge ${b.merge}`].filter(Boolean).join(" · ") + ` — ${clip(r.title, 70)}` + (!quiet && b.lastNote ? ` — ${clip(b.lastNote, 180)}` : "")
}
function brief(r: any): any {
  const rn = r.runner || {}
  const note = (r.updates || []).find((u: any) => !u.auto && u.kind === "update" && u.event !== "owner-message")
  return {
    repo: r.repo, id: r.id, title: clip(r.title, 90), status: r.status,
    run: rn.state ? `${rn.state}${rn.command ? " · " + rn.command : ""}${rn.account ? " · account " + rn.account : ""}` : undefined,
    phase: r.phase && r.phase !== "plan" ? `${r.phase}${r.phaseName ? " · " + r.phaseName : ""}` : undefined,
    pr: r.pr ? `#${r.pr.number} ${r.pr.state}` : undefined,
    openQuestions: (r.questions || []).length || undefined,
    deferredOpen: r.deferredOpen || undefined,
    merge: r.merge ? `${r.merge.state}${r.merge.error ? ": " + clip(r.merge.error, 160) : ""}` : undefined,
    lastNote: note ? `${note.ts} ${clip(note.text, 300)}` : undefined,
  }
}
const findRun = async (repo: string, id: string): Promise<any> => {
  const r = await api("/api/runs")
  const run = (r.body?.runs || []).find((x: any) => x.repo === repo && x.id === id)
  if (!run) throw new Error(`no dossier ${id} in ${repo}`)
  return run
}

const P = {
  repo: { name: "repo", type: "string", description: "The repository name, e.g. config-harness" } as Param,
  id: { name: "id", type: "string", description: "The dossier id, e.g. W-128" } as Param,
  qid: { name: "id", type: "string", description: "The question id, e.g. config-harness.W-128.Q15" } as Param,
}

const TOOLS: Tool[] = [
  // ---- read ----
  {
    name: "runs_overview",
    description: "What is running now, across repos: live runs (running, waiting, paused, queued), one line each with its state, command, account, phase, PR and open questions, and the last note for a run that is not queued; /plan runs of deferred items that have no dossier yet; each account's 5-hour and weekly use; how many questions wait for the owner. Add all=\"yes\" to list every dossier, not only live ones.",
    params: [{ name: "all", type: "string", description: "\"yes\" for every dossier", optional: true }],
    async run(a) {
      const d = (await api("/api/runs")).body
      const runs = (d.runs || []).filter((r: any) => a.all === "yes" || LIVE.includes(r.runner?.state))
      const q = (await api("/api/questions?state=open")).body
      // One line per run, unindented: a dashboard with a dozen live runs must still fit one tool result whole (the
      // DM agent's context-mode hook cuts a long result, and a cut overview loses runs).
      const count: Record<string, number> = {}
      for (const r of runs) { const s = r.runner?.state || r.status || "?"; count[s] = (count[s] || 0) + 1 }
      return JSON.stringify({
        scannedAt: d.scannedAt, openQuestions: Array.isArray(q) ? q.length : undefined,
        counts: count,
        runs: runs.map(line),
        planning: (d.plans || []).map((p: any) => ({ key: p.key, from: `${p.src} ${p.item}`, state: p.state, title: clip(p.title, 80), last: clip(p.last, 200) })),
        accounts: (d.accounts || []).map((x: any) => ({ name: x.name, next: x.next || undefined, blockedUntil: x.limitedUntil || undefined,
          fiveHour: x.fiveHour?.usedPct ?? null, week: x.week?.usedPct ?? null, sameLoginAs: x.sameLoginAs, runs: x.runs })),
      }, null, 0).replace(/,"(runs|planning|accounts)":/g, ',\n"$1":').replace(/","/g, '",\n "')
    },
  },
  {
    name: "run_detail",
    description: "One dossier: its state, the timeline per run (Implement dossier, Fix deferred issues, …) with each phase or step and its newest entries, the final reply and chat, blocked calls, and the product summary's first lines.",
    params: [P.repo, P.id, { name: "entries", type: "string", description: "newest entries per section, default 12", optional: true }],
    async run(a) {
      const t = (await api(`/api/timeline/${enc(a.repo)}/${enc(a.id)}`))
      if (t.status >= 300) return `refused (HTTP ${t.status}): ${out(t.body)}`
      const d = t.body; const n = Math.max(1, Math.min(60, Number(a.entries) || 12))
      const items = (g: any) => (g.items || []).flatMap((i: any) => (i.kind === "more" ? i.entries.map((e: any) => e.entry) : [i.entry])).slice(0, n)
        .map((e: any) => `${e.ts} ${e.type}${e.event ? "/" + e.event : ""}: ${clip(e.text, 240)}`)
      return out({
        run: brief(d.run),
        now: d.now ? { phase: d.now.phase, doing: d.now.doing?.text ? clip(d.now.doing.text, 200) : undefined } : undefined,
        sections: (d.sections || []).map((s: any) => ({ section: s.label, groups: s.groups.slice(0, 4).map((g: any) => ({ group: g.label, entries: items(g) })) })),
        result: d.conv?.result ? clip(d.conv.result.text, 1500) : undefined,
        chat: (d.conv?.chat || []).slice(-6).map((l: any) => `${l.from}: ${clip(l.text, 400)}`),
        blocked: (d.blocks || []).map((b: any) => ({ tool_use_id: b.id, tool: b.tool, target: clip(b.target, 300), reason: clip(b.reason, 200) })),
        summary: d.summary ? clip(d.summary.html?.replace(/<[^>]+>/g, " ").replace(/\s+/g, " "), 600) : undefined,
      })
    },
  },
  {
    name: "deferred",
    description: "A finished dossier's deferred items: each D-id, its kind, title, state (open, fixing, fixed, dossier, deprecated), the owner's ruling, its new dossier or planning run, and the stacked PR.",
    params: [P.repo, P.id],
    async run(a) {
      const r = await findRun(a.repo, a.id)
      const df = r.deferred || { items: [] }
      return out({ pr: df.pr || undefined, branch: df.branch || undefined, items: df.items.map((i: any) => ({ id: i.id, kind: i.kind, title: clip(i.title, 120), state: i.state,
        ruling: i.ruling ? clip(i.ruling, 160) : undefined, keptOpen: i.keptOpen || undefined, dossier: i.dossier || undefined, planning: i.planning, contract: i.contract, effort: i.effort })) })
    },
  },
  {
    name: "stubs",
    description: "Each repo's open stubs: the dossiers merged into it, and the ones on it still in progress or with a PR waiting for the owner's merge. For an integrating stub's progress, call stub_detail.",
    params: [{ name: "repo", type: "string", description: "only this repo", optional: true }],
    async run(a) {
      const d = (await api("/api/stubs")).body
      return out((d.repos || []).filter((r: any) => !a.repo || r.repo === a.repo).map((r: any) => ({
        repo: r.repo, open: r.stubs.filter((s: any) => r.open.includes(s.branch)).map((s: any) => ({ branch: s.branch, state: s.state, stage: s.stage || undefined,
          pr: s.pr ? `#${s.pr.number} ${s.pr.state}` : undefined, merged: s.dossiers.map((x: any) => x.id), onIt: s.members.map((m: any) => m.id + (m.noPr ? " (no PR yet)" : "")) })),
      })))
    },
  },
  {
    name: "questions",
    description: "The owner's questions: open ones (default) or all, oldest first, with their options and the agent's recommendation.",
    params: [{ name: "state", type: "string", description: "open or all", optional: true, enum: ["open", "all"] }],
    async run(a) {
      const d = (await api(`/api/questions${a.state === "all" ? "" : "?state=open"}`)).body
      return out((Array.isArray(d) ? d : []).sort((x: any, y: any) => String(x.ts).localeCompare(String(y.ts))).map((q: any) => ({
        id: q.id, dossier: q.dossier, state: q.state, asked: q.ts, text: clip(q.text, 700),
        options: (q.options || []).map((o: any) => `${o.key}: ${o.label}`), recommendation: q.recommendation || undefined,
        thread: (q.lines || []).slice(1).length || undefined })))
    },
  },
  {
    name: "question",
    description: "One question in full: its text, options, recommendation and the whole thread (the owner's messages and the agent's replies).",
    params: [P.qid],
    async run(a) {
      const r = await api(`/api/questions/${enc(a.id)}`)
      if (r.status >= 300) return `refused (HTTP ${r.status}): ${out(r.body)}`
      const q = r.body
      return out({ id: q.id, state: q.state, dossier: q.dossier, text: q.text, options: q.options, recommendation: q.recommendation, blocks: q.blocks,
        thread: (q.lines || []).slice(1).map((l: any) => `${l.ts} ${l.from} ${l.kind}${l.option ? " [" + l.option + "]" : ""}: ${clip(l.text, 1200)}`) })
    },
  },
  {
    name: "stub_detail",
    description: "One stub's integration (/stub) as its page shows it: the runner state, the step it is on and what it is doing, the merge-queue part of Integrate, open questions, and the newest timeline entries (suite runs, fix rounds, audit). The progress of a stub run lives here, not in run_detail.",
    params: [P.repo, { name: "branch", type: "string", description: "the stub branch, e.g. stub/2026-11-08" },
      { name: "entries", type: "string", description: "newest entries, default 20", optional: true }],
    async run(a) {
      const r = await api(`/api/stub?repo=${enc(a.repo)}&branch=${enc(a.branch)}`)
      if (r.status >= 300) return `refused (HTTP ${r.status}): ${out(r.body)}`
      const d = r.body; const n = Math.max(1, Math.min(80, Number(a.entries) || 20))
      const rn = d.runner || {}
      return out({
        stub: { branch: d.stub?.branch, state: d.stub?.state, stage: d.stub?.stage || undefined, pr: d.stub?.pr ? `#${d.stub.pr.number} ${d.stub.pr.state}` : undefined, stopped: d.stub?.stopped || undefined },
        run: rn.state ? { state: rn.state, account: rn.account, started: rn.started, questions: rn.questions?.length ? rn.questions : undefined } : "no /stub run yet",
        now: d.now ? { phase: d.now.phase, doing: d.now.doing?.text ? clip(d.now.doing.text, 300) : undefined } : undefined,
        integrate: d.integrate || undefined,
        entries: (d.entries || []).filter((e: any) => e.type !== "boundary").slice(-n).reverse().map((e: any) => `${e.ts ?? ""} ${e.type ?? ""}${e.event ? "/" + e.event : ""}: ${clip(e.text, 260)}`),
      })
    },
  },
  {
    name: "runner_log",
    description: "The tail of a dossier's runner log (the runner process's own output): stages, waits, failures.",
    params: [P.repo, P.id, { name: "lines", type: "string", description: "how many last lines, default 40", optional: true }],
    async run(a) {
      const r = await api(`/api/runs/${enc(a.repo)}/${enc(a.id)}/log`)
      if (r.status >= 300) return `refused (HTTP ${r.status}): ${out(r.body)}`
      return String(r.body).split("\n").slice(-Math.max(1, Math.min(400, Number(a.lines) || 40))).join("\n")
    },
  },
  // ---- act: each one does what the dashboard's button does ----
  {
    name: "answer_question",
    description: "Answers an owner question with one of its option keys, as pressing that button on the dashboard. It closes the question, and the agent that asked goes on with that answer.",
    params: [P.qid, { name: "option", type: "string", description: "the option key, e.g. A" }],
    run: (a) => act(`/api/questions/${enc(a.id)}/messages`, { kind: "answer", option: a.option }),
  },
  {
    name: "reply_question",
    description: "Writes a message in a question's chat (a follow-up, as typing in its box). The question stays open; the agent replies, and adds a button for its reading of the message when it reads as an answer.",
    params: [P.qid, { name: "text", type: "string", description: "the message" }],
    run: (a) => act(`/api/questions/${enc(a.id)}/messages`, { kind: "followup", text: a.text }),
  },
  {
    name: "message_run",
    description: "Sends the owner's message to a dossier's agent without a question: to a live run it goes into the running session at once (it outranks an earlier answer); to a finished run it resumes the session that wrote the final reply.",
    params: [P.repo, P.id, { name: "text", type: "string", description: "the message" }],
    run: (a) => act(`/api/runs/${enc(a.repo)}/${enc(a.id)}/chat`, { text: a.text }),
  },
  {
    name: "run_control",
    description: "Starts, resumes or stops a dossier's run on the runner, or starts fixing its deferred items. start runs the next flow (/plan for a planned dossier, /work-on for a ready one); resume reruns the flow that stopped; stop ends the live run; fix-deferred runs /fix-deferred on the open items (all, or the listed D-ids) in one stacked PR. These start real agent sessions that spend tokens.",
    params: [P.repo, P.id, { name: "action", type: "string", description: "start, resume, stop or fix-deferred", enum: ["start", "resume", "stop", "fix-deferred"] },
      { name: "items", type: "array", description: "fix-deferred only: D-ids, e.g. [\"D-1\",\"D-3\"]", optional: true }],
    run: (a) => act(`/api/runs/${enc(a.repo)}/${enc(a.id)}/${a.action}`, a.action === "fix-deferred" && Array.isArray(a.items) ? { items: a.items } : {}),
  },
  {
    name: "plan_item",
    description: "Starts a /plan run for one deferred item the owner ruled to its own dossier (Plan it on the Deferred tab). It writes a new dossier.",
    params: [P.repo, P.id, { name: "item", type: "string", description: "the D-id, e.g. D-3" }],
    run: (a) => act(`/api/runs/${enc(a.repo)}/${enc(a.id)}/plan-item`, { item: a.item }),
  },
  {
    name: "merge_preview",
    description: "What the Merge button would do for a dossier, changing nothing: which of its PRs merge into which stub, or why it cannot merge.",
    params: [P.repo, P.id],
    run: (a) => act(`/api/runs/${enc(a.repo)}/${enc(a.id)}/merge`, undefined, "GET"),
  },
  {
    name: "merge",
    description: "Puts a dossier in its repository's merge queue (first in, first out), as the Merge button. The queue merges ALL of the dossier's open PRs into its stub on GitHub as one stack: conflicts first go to the dossier's agent (/resolve-merge), then the script merges, then /after-merge runs, then the next dossier. Irreversible once merged: check merge_preview first and merge only when the owner asked for it. cancel=\"yes\" takes it out of the queue again.",
    params: [P.repo, P.id, { name: "cancel", type: "string", description: "\"yes\" to leave the queue", optional: true }],
    run: (a) => act(`/api/runs/${enc(a.repo)}/${enc(a.id)}/merge`, a.cancel === "yes" ? { cancel: true } : {}),
  },
  {
    name: "block_ruling",
    description: "Rules on a call the auto-mode safety check refused in a run (the Blocked card): once allows that exact call one time, always saves a permission rule in the repo's .claude/settings.local.json (the rule in `rule`, default the exact command), deny refuses it.",
    params: [P.repo, P.id, { name: "tool_use_id", type: "string", description: "the blocked call's id, from run_detail" },
      { name: "action", type: "string", description: "once, always or deny", enum: ["once", "always", "deny"] },
      { name: "rule", type: "string", description: "always only: the permission rule, e.g. Bash(limactl shell vm -- docker compose build *)", optional: true }],
    run: (a) => act(`/api/runs/${enc(a.repo)}/${enc(a.id)}/blocks/${enc(a.tool_use_id)}`, { action: a.action, ...(a.rule ? { rule: a.rule } : {}) }),
  },
  {
    name: "write_summary",
    description: "Writes (or rewrites) a finished dossier's product summary, the page shown above the dossier. Starts one short agent session.",
    params: [P.repo, P.id],
    run: (a) => act(`/api/runs/${enc(a.repo)}/${enc(a.id)}/summary`, {}),
  },
  {
    name: "refresh_usage",
    description: "Reads every account's current 5-hour and weekly use again (one tiny Haiku call per account).",
    params: [],
    run: () => act("/api/accounts/refresh", {}),
  },
]

/**
 * Only sessions in this plugin's own repository get the tools (any worktree of it): the server can merge, start and
 * stop runs, and answer for the owner, so no session in a target repository (a runner stage, an implementer) may
 * drive it. The session's project is CLAUDE_PROJECT_DIR (Claude Code sets it), else the cwd; its main checkout's
 * name must be in ASD_RUNS_MCP_REPOS (default "agentic-software-development"). Elsewhere tools/list is empty.
 */
export function allowedHere(dir = process.env.CLAUDE_PROJECT_DIR || process.cwd()): boolean {
  const ok = (process.env.ASD_RUNS_MCP_REPOS || "agentic-software-development").split(",").map((x) => x.trim()).filter(Boolean)
  const r = Bun.spawnSync(["git", "-C", dir, "rev-parse", "--path-format=absolute", "--git-common-dir"], { stdout: "pipe", stderr: "ignore" })
  const common = r.exitCode === 0 ? r.stdout.toString().trim() : ""
  const main = common ? common.replace(/\/\.git\/?$/, "") : ""
  return !!main && ok.includes(main.split("/").filter(Boolean).pop() ?? "")
}
const ALLOWED = allowedHere()

export const TOOL_NAMES = TOOLS.map((t) => t.name)
const schema = (t: Tool) => ({
  type: "object",
  properties: Object.fromEntries(t.params.map((p) => [p.name, { type: p.type, description: p.description, ...(p.type === "array" ? { items: { type: "string" } } : {}), ...(p.enum ? { enum: p.enum } : {}) }])),
  required: t.params.filter((p) => !p.optional).map((p) => p.name),
})
function checkArgs(t: Tool, args: unknown): string | undefined {
  if (!args || typeof args !== "object" || Array.isArray(args)) return "arguments must be an object"
  const a = args as Record<string, unknown>
  for (const p of t.params) {
    const v = a[p.name]
    if (v === undefined || v === null || v === "") { if (!p.optional) return `missing required argument "${p.name}"`; continue }
    if (p.type === "array" ? !Array.isArray(v) : typeof v !== "string") return `argument "${p.name}" must be ${p.type === "array" ? "an array" : "a string"}`
    if (p.enum && !p.enum.includes(v as string)) return `argument "${p.name}" must be one of ${p.enum.join(", ")}`
  }
  return undefined
}
async function callTool(name: string, args: unknown) {
  const t = TOOLS.find((x) => x.name === name)
  if (!t) return undefined
  const bad = checkArgs(t, args)
  if (bad) return text(`${name}: ${bad}`, true)
  try {
    const s = await t.run(args as Record<string, any>)
    return text(s, s.startsWith("refused"))
  } catch (e) {
    return text(`${name}: ${(e as Error).message}`, true)
  }
}
async function handle(msg: Rpc): Promise<object | undefined> {
  const reply = (result: object) => ({ jsonrpc: "2.0", id: msg.id, result })
  const fail = (code: number, message: string) => ({ jsonrpc: "2.0", id: msg.id ?? null, error: { code, message } })
  if (msg.id === undefined) return undefined
  switch (msg.method) {
    case "initialize": {
      const asked = msg.params?.protocolVersion
      return reply({ protocolVersion: SUPPORTED.includes(asked) ? asked : SUPPORTED[0], capabilities: { tools: {} }, serverInfo: SERVER })
    }
    case "ping":
      return reply({})
    case "tools/list":
      return reply({ tools: ALLOWED ? TOOLS.map((t) => ({ name: t.name, description: t.description, inputSchema: schema(t) })) : [] })
    case "tools/call": {
      if (!ALLOWED) return reply(text("the runs tools work only in sessions in the agentic-software-development repository (ASD_RUNS_MCP_REPOS)", true))
      const result = await callTool(String(msg.params?.name ?? ""), msg.params?.arguments ?? {})
      return result ? reply(result) : fail(-32602, `unknown tool: ${msg.params?.name}`)
    }
    default:
      return fail(-32601, `method not found: ${msg.method}`)
  }
}

if (import.meta.main) {
  let pending = Promise.resolve()
  const onLine = (line: string) => {
    if (!line.trim()) return
    let msg: Rpc
    try { msg = JSON.parse(line) } catch {
      process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "parse error" } }) + "\n")
      return
    }
    pending = pending.then(async () => { const o = await handle(msg); if (o) process.stdout.write(JSON.stringify(o) + "\n") })
  }
  const decoder = new TextDecoder()
  let buffer = ""
  for await (const chunk of Bun.stdin.stream()) {
    buffer += decoder.decode(chunk, { stream: true })
    let at: number
    while ((at = buffer.indexOf("\n")) !== -1) { onLine(buffer.slice(0, at)); buffer = buffer.slice(at + 1) }
  }
  onLine(buffer)
  await pending
}
