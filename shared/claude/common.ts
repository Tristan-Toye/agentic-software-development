import { appendFileSync, closeSync, mkdirSync, openSync, readFileSync, readSync, realpathSync, writeFileSync } from "node:fs"
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
