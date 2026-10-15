import { appendFileSync, closeSync, existsSync, mkdirSync, openSync, readdirSync, readFileSync, readSync, realpathSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join, resolve } from "node:path"
import { createReadStream } from "node:fs"
import { createInterface } from "node:readline"

// Plumbing shared by the Claude Code hook adapters (pre-tool-use, post-tool-use,
// session-start). Every hook fails open: any error is exit 0 with no output, so a
// broken hook never blocks a call.

export type HookInput = {
  session_id?: string
  transcript_path?: string
  cwd?: string
  agent_id?: string
  agent_type?: string
  tool_name?: string
  tool_input?: Record<string, any>
  source?: string
}

export const dataDir = () =>
  join(process.env.XDG_DATA_HOME ?? join(homedir(), ".local", "share"), "agentic-software-development", "claude")

// The pipeline repo / plugin root: CLAUDE_PLUGIN_ROOT, else two levels above shared/claude/.
export const pipelineRoot = () => process.env.CLAUDE_PLUGIN_ROOT ?? resolve(dirname(realpathSync(import.meta.path)), "..", "..")

// The single config, shared with opencode; CLAUDE_CONTEXT_GUARD_CONFIG points at another file.
export const configPath = () =>
  process.env.CLAUDE_CONTEXT_GUARD_CONFIG ?? join(dirname(realpathSync(import.meta.path)), "..", "context-guard.yaml")

// One line per event, in context-guard.log's style: `<iso> <event> <session> <detail>`.
// A log that cannot be written never breaks a hook.
export function log(event: string, session: string, detail: string) {
  try {
    const file = join(dataDir(), "claude-hooks.log")
    mkdirSync(dirname(file), { recursive: true })
    appendFileSync(file, `${new Date().toISOString()} ${event} ${session} ${detail.replace(/\s+/g, " ")}\n`)
  } catch {
    // nothing to do
  }
}

// Reads stdin as JSON; undefined for anything else.
export async function readInput(): Promise<HookInput | undefined> {
  try {
    const parsed = JSON.parse(await Bun.stdin.text())
    return parsed && typeof parsed === "object" ? (parsed as HookInput) : undefined
  } catch {
    return undefined
  }
}

// Runs a hook body: prints its JSON result, and on any error prints nothing. Exit code 0 always.
export async function runHook(body: () => Promise<object | undefined> | object | undefined): Promise<void> {
  try {
    const out = await body()
    if (out) process.stdout.write(JSON.stringify(out) + "\n")
  } catch {
    // fail open
  }
  process.exit(0)
}

// ---- the subagent's type --------------------------------------------------------

// A subagent's type, plugin-scoped (`agentic-software-development:unit-test-author`): the
// hook input's `agent_type` when Claude Code sends it, else the `agentType` of the
// subagent's own record — `<session>/subagents/[workflows/<run>/]agent-<id>.meta.json`,
// beside the session transcript — which is where an agent a workflow spawned is recorded.
export function agentTypeOf(input: HookInput): string | undefined {
  if (input.agent_type) return input.agent_type
  if (!input.agent_id || !input.session_id || !input.transcript_path) return undefined
  const want = `agent-${input.agent_id}.meta.json`
  // The session's transcript, or the agent's own one beside its record.
  const own = join(dirname(input.transcript_path), want)
  const base = join(dirname(input.transcript_path), input.session_id, "subagents")
  try {
    let file = existsSync(own) ? own : undefined
    if (!file) {
      const found = (readdirSync(base, { recursive: true }) as string[]).find((f) => f === want || f.endsWith("/" + want))
      if (found) file = join(base, found)
    }
    if (!file) return undefined
    const type = JSON.parse(readFileSync(file, "utf8"))?.agentType
    return typeof type === "string" ? type : undefined
  } catch {
    return undefined
  }
}

// ---- per-session state ----------------------------------------------------------

export type SessionState = { orchestrator?: boolean; calls?: number }
const stateFile = (session: string) => join(dataDir(), `${session.replace(/[^\w.-]/g, "_")}.json`)

export function loadState(session: string): SessionState {
  try {
    return JSON.parse(readFileSync(stateFile(session), "utf8")) as SessionState
  } catch {
    return {}
  }
}

export function saveState(session: string, state: SessionState) {
  try {
    mkdirSync(dataDir(), { recursive: true })
    writeFileSync(stateFile(session), JSON.stringify(state))
  } catch {
    // the answer is recomputed next time
  }
}

// ---- the transcript -------------------------------------------------------------

const textOf = (content: unknown): string =>
  typeof content === "string"
    ? content
    : Array.isArray(content)
      ? content.map((b: any) => (typeof b?.text === "string" ? b.text : "")).join("\n")
      : ""

// The /work-on command as the transcript records it: `<command-name>/<plugin>:work-on</command-name>`.
const WORK_ON_RE = /<command-name>\/?(?:[\w.-]+:)?work-on<\/command-name>/

// Reads the head of the transcript (first 1 MB, whole lines) as parsed lines.
function head(path: string): any[] {
  const fd = openSync(path, "r")
  try {
    const buf = Buffer.alloc(1 << 20)
    const n = readSync(fd, buf, 0, buf.length, 0)
    const lines = buf.toString("utf8", 0, n).split("\n")
    if (n === buf.length) lines.pop() // the last line may be cut
    const out: any[] = []
    for (const line of lines) {
      try {
        out.push(JSON.parse(line))
      } catch {
        // not a record
      }
    }
    return out
  } finally {
    closeSync(fd)
  }
}

// Whether this session ran the /work-on command, decided once from the transcript head
// and cached in the session's state file. An empty transcript (no user message yet) is
// not cached: the answer may change.
export function isOrchestrator(input: HookInput): boolean {
  if (input.agent_id || !input.session_id) return false
  const state = loadState(input.session_id)
  if (typeof state.orchestrator === "boolean") return state.orchestrator
  if (!input.transcript_path) return false
  const records = head(input.transcript_path)
  const users = records.filter((r) => r?.type === "user" && !r.isSidechain)
  const found = users.some((r) => WORK_ON_RE.test(textOf(r.message?.content)))
  if (found || users.length > 0) saveState(input.session_id, { ...state, orchestrator: found })
  return found
}

export const isCheckpointTool = (name: unknown) =>
  typeof name === "string" && (name === "phase_checkpoint" || name.endsWith("__phase_checkpoint"))

export type Recorded = { phase: string; summary: string }

// The phase_checkpoint calls of a transcript, in order, and the session's original task
// (the first user prompt). Streams line by line: a transcript can be tens of MB, and a
// line is parsed only when it can matter. A call whose result was an error is dropped.
export async function readCheckpoints(path: string): Promise<{ task: string; checkpoints: Recorded[] }> {
  const calls = new Map<string, Recorded>()
  let task = ""
  const rl = createInterface({ input: createReadStream(path, { encoding: "utf8" }), crlfDelay: Infinity })
  for await (const line of rl) {
    const isCall = line.includes("phase_checkpoint") && line.includes('"tool_use"')
    const isFailure = line.includes('"tool_result"') && line.includes('"is_error":true')
    const wantsTask = task === "" && line.includes('"type":"user"')
    if (!isCall && !isFailure && !wantsTask) continue
    let rec: any
    try {
      rec = JSON.parse(line)
    } catch {
      continue
    }
    if (rec?.isSidechain) continue
    const content = rec?.message?.content
    if (wantsTask && rec?.type === "user" && !rec.isMeta) {
      const t = textOf(content).trim()
      if (t) task = t
    }
    if (!Array.isArray(content)) continue
    for (const block of content) {
      if (isCall && block?.type === "tool_use" && isCheckpointTool(block.name) && typeof block.input?.phase === "string") {
        calls.set(String(block.id), { phase: block.input.phase, summary: String(block.input.summary ?? "") })
      } else if (isFailure && block?.type === "tool_result" && block.is_error === true) {
        calls.delete(String(block.tool_use_id))
      }
    }
  }
  return { task, checkpoints: [...calls.values()] }
}

// ---- the work in flight ----------------------------------------------------------

export type InFlight = {
  kind: "Agent" | "Workflow"
  label: string
  type?: string
  agentId?: string
  runId?: string
  taskId?: string
  arrived: boolean
}

const SPAWN_TOOLS = ["Agent", "Task", "Workflow"]
const DOSSIER_PATH_RE = /(\/[^\s"'\\]*\.discovery\/dossiers\/[\w.-]+\.md)/g

const idOf = (text: string, key: string): string | undefined =>
  new RegExp(`${key}["']?\\s*[:=]\\s*["']?([\\w-]+)`).exec(text)?.[1]

// Every Agent/Task spawn and Workflow launch since the last valid phase_checkpoint call,
// with the agent or run ID the launch result carried and whether its result arrived: a
// synchronous spawn arrives with its tool result, a background spawn or a workflow run
// with a later <task-notification> that names its ID. Also the dossier the session has
// been working on (the last `.discovery/dossiers/*.md` path any tool call named) and the
// /work-on arguments. Streams line by line and parses only lines that can matter.
export async function readInFlight(path: string): Promise<{ items: InFlight[]; dossier?: string; args: string }> {
  type Item = InFlight & { seq: number; callId: string }
  const items: Item[] = []
  const checkpoints = new Map<string, number>()
  let dossier: string | undefined
  let args = ""
  let seq = 0
  const rl = createInterface({ input: createReadStream(path, { encoding: "utf8" }), crlfDelay: Infinity })
  for await (const line of rl) {
    seq++
    if (line.includes('"tool_use"') && line.includes(".discovery/dossiers/")) {
      const found = [...line.matchAll(DOSSIER_PATH_RE)].map((m) => m[1])
      if (found.length > 0) dossier = found[found.length - 1]
    }
    const isCall = line.includes('"tool_use"') && (line.includes("phase_checkpoint") || SPAWN_TOOLS.some((t) => line.includes(`"name":"${t}"`)))
    const pending = items.filter((i) => !i.arrived || (!i.agentId && !i.runId))
    const isResult = pending.length > 0 && line.includes('"tool_result"') && pending.some((i) => line.includes(i.callId))
    const isNotice = line.includes("task-notification") && items.length > 0
    const isArgs = args === "" && line.includes("<command-args>")
    const isFailure = checkpoints.size > 0 && line.includes('"is_error":true') && line.includes('"tool_result"')
    if (!isCall && !isResult && !isNotice && !isArgs && !isFailure) continue
    let rec: any
    try {
      rec = JSON.parse(line)
    } catch {
      continue
    }
    if (rec?.isSidechain) continue
    const content = rec?.message?.content
    if (isArgs && rec?.type === "user") {
      const m = /<command-args>([\s\S]*?)<\/command-args>/.exec(textOf(content))
      if (m && WORK_ON_RE.test(textOf(content))) args = m[1].trim()
    }
    if (!Array.isArray(content)) {
      if (isNotice && rec?.type === "user") {
        const text = textOf(content)
        for (const item of items) {
          if ([item.agentId, item.runId, item.taskId].some((id) => id && text.includes(id))) item.arrived = true
        }
      }
      continue
    }
    for (const block of content) {
      if (block?.type === "tool_use") {
        if (isCheckpointTool(block.name)) {
          checkpoints.set(String(block.id), seq)
        } else if (SPAWN_TOOLS.includes(block.name)) {
          const input = block.input ?? {}
          const workflow = block.name === "Workflow"
          items.push({
            kind: workflow ? "Workflow" : "Agent",
            label: String((workflow ? (input.name ?? input.scriptPath ?? "workflow") : (input.description ?? input.name ?? "agent")) ?? "").slice(0, 80),
            type: workflow ? undefined : typeof input.subagent_type === "string" ? input.subagent_type : undefined,
            arrived: false,
            seq,
            callId: String(block.id),
          })
        }
      } else if (block?.type === "tool_result") {
        const id = String(block.tool_use_id)
        if (block.is_error === true && checkpoints.has(id)) {
          checkpoints.delete(id)
          continue
        }
        const item = items.find((i) => i.callId === id)
        if (!item) continue
        const text = textOf(block.content)
        item.agentId = idOf(text, "agentId") ?? item.agentId
        item.runId = idOf(text, "runId") ?? item.runId
        item.taskId = idOf(text, "taskId") ?? item.taskId
        const background = item.kind === "Workflow" || /async_launched|launched successfully|running in the background/i.test(text)
        item.arrived = block.is_error === true || !background
      } else if (isNotice && block?.type === "text") {
        for (const item of items) {
          if ([item.agentId, item.runId, item.taskId].some((id) => id && String(block.text).includes(id))) item.arrived = true
        }
      }
    }
  }
  const cutoff = Math.max(0, ...checkpoints.values())
  return { items: items.filter((i) => i.seq > cutoff), dossier, args }
}

// The last `lines` lines of the dossier's `## Build log`, each cut to 300 characters; when
// the transcript named no dossier path, `args` (the /work-on argument, a dossier ID) is
// looked up under `<cwd>/.discovery/dossiers/`. undefined on any failure.
export function buildLogTail(dossier: string | undefined, args: string, cwd: string | undefined, lines = 40): { path: string; text: string } | undefined {
  try {
    let path = dossier
    if (!path || !existsSync(path)) {
      const id = /[A-Za-z]+-\d+/.exec(args)?.[0]
      if (!id || !cwd) return undefined
      const dir = join(cwd, ".discovery", "dossiers")
      const file = readdirSync(dir).find((f) => f.startsWith(id) && f.endsWith(".md"))
      if (!file) return undefined
      path = join(dir, file)
    }
    const all = readFileSync(path, "utf8").split("\n")
    const start = all.findIndex((l) => /^## Build log\s*$/.test(l))
    if (start < 0) return undefined
    let end = all.findIndex((l, i) => i > start && /^## /.test(l))
    if (end < 0) end = all.length
    const body = all.slice(start + 1, end).filter((l) => l.trim() !== "")
    if (body.length === 0) return undefined
    return { path, text: body.slice(-lines).map((l) => (l.length > 300 ? l.slice(0, 300) + " …" : l)).join("\n") }
  } catch {
    return undefined
  }
}
