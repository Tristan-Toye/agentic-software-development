// The owner's Slack DM agent: a message in the DM outside a question's thread goes to a Claude session that answers
// it with the runs MCP server (shared/claude/runs-mcp.ts) and, like a manual Claude Code session, can edit, commit and
// open PRs in the repos (permission mode `auto`: a classifier approves or blocks each action). A new top-level message starts a new session; a reply in
// that message's thread resumes the same session. The prompt is references/dm-agent.md.
// Contract: references/owner-questions.md (Slack, "The DM agent").
import { randomUUID } from "node:crypto"
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { basename, dirname, join } from "node:path"

export type DmSession = { sessionId: string; configDir: string; started: string; last: string; turns: number; cwd?: string }
export type DmStore = Record<string, DmSession>   // key: "<channel>:<thread root ts>"

const home = (): string => process.env.HOME || homedir()
export const dmStoreFile = (): string => process.env.ASD_DM_AGENT_FILE || join(home(), ".agentic", "dm-agent.json")
export const pluginRoot = (): string => join(import.meta.dir, "..", "..")
export const promptFile = (): string => join(pluginRoot(), "references", "dm-agent.md")

export function readStore(f = dmStoreFile()): DmStore {
  try { return JSON.parse(readFileSync(f, "utf8")) ?? {} } catch { return {} }
}
export function writeStore(s: DmStore, f = dmStoreFile()): void {
  mkdirSync(dirname(f), { recursive: true })
  writeFileSync(f + ".tmp", JSON.stringify(s, null, 1))
  renameSync(f + ".tmp", f)
}

export const DEFAULT_MODEL = "opus"
export const DEFAULT_PERMISSION_MODE = "auto"
export const DEFAULT_TIMEOUT_MS = 60 * 60_000
export const PROGRESS_EVERY_MS = 20_000

/** The `claude` argv for one turn: a new session with its own id, or `--resume` of the thread's session. */
export function claudeArgs(o: { sessionId: string; resume: boolean; model: string; addDirs: string[]; permissionMode?: string }): string[] {
  const bin = (process.env.ASD_CLAUDE_BIN || "claude").split(/\s+/).filter(Boolean)
  return [
    ...bin, "-p", "--output-format", "stream-json", "--verbose", "--model", o.model,
    ...(o.resume ? ["--resume", o.sessionId] : ["--session-id", o.sessionId]),
    "--append-system-prompt-file", promptFile(),
    "--permission-mode", o.permissionMode || process.env.ASD_DM_AGENT_PERMISSION_MODE || DEFAULT_PERMISSION_MODE,
    ...o.addDirs.flatMap((d) => ["--add-dir", d]),
  ]
}

/**
 * The working directory of a new session: the main checkout of the repo the message names (by its directory name,
 * the longest match wins), else the plugin root. It is stored with the session: Claude keeps a session under its
 * cwd, so a resume must start from the same one.
 */
export function pickCwd(text: string, dirs: string[], fallback = pluginRoot()): string {
  const esc = (x: string) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
  let best = "", bestLen = 0
  for (const d of dirs) {
    const name = basename(d)
    if (name.length > bestLen && new RegExp(`(^|[^\\w-])${esc(name)}($|[^\\w-])`, "i").test(text)) { best = d; bestLen = name.length }
  }
  return best || fallback
}

/** What the stream says so far, for the placeholder's status line. */
export class Progress {
  turns = 0
  lastTool = ""
  result: any = null
  constructor(private t0: number) {}
  /** Feed one stdout line of `--output-format stream-json`; lines that are not JSON are ignored. */
  feed(line: string): void {
    let j: any
    try { j = JSON.parse(line) } catch { return }
    if (j?.type === "assistant") {
      this.turns++
      for (const c of j.message?.content ?? []) if (c?.type === "tool_use" && typeof c.name === "string") this.lastTool = c.name.replace(/^mcp__plugin_[^_]+(?:-[^_]+)*_/, "").replace(/__/g, ":")
    } else if (j?.type === "result") this.result = j
  }
  status(now: number): string {
    const s = Math.max(0, Math.round((now - this.t0) / 1000))
    const el = s >= 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s} s`
    return `_Working… ${el}, turn ${this.turns}${this.lastTool ? `, last tool: \`${this.lastTool}\`` : ""}_`
  }
}

/** The answer from `--output-format stream-json` (the `result` event) or plain json: `result`, or why there is none. */
export function parseResult(stdout: string, stderr: string, code: number | null): { ok: boolean; text: string } {
  let j: any = null
  const lines = stdout.trim().split("\n")
  for (let i = lines.length - 1; i >= 0 && !j; i--) {
    try { const x = JSON.parse(lines[i]); if (x && (x.type === "result" || i === lines.length - 1)) j = x } catch { /* not json */ }
  }
  if (j && typeof j.result === "string" && !j.is_error) return { ok: true, text: j.result.trim() || "(no answer)" }
  const why = (j?.result || j?.subtype || stderr || stdout || `exit ${code}`).toString().trim()
  return { ok: false, text: why.slice(0, 1500) }
}

/** Slack's message text limit is 40 000 characters; keep well under it. */
export const clipForSlack = (s: string, n = 38_000): string => (s.length > n ? s.slice(0, n - 40) + "\n…(cut; ask for the rest)" : s)

export type SpawnResult = { stdout: string; stderr: string; code: number | null; timedOut?: boolean }
export type DmDeps = {
  post: (channel: string, threadTs: string, text: string) => Promise<string>   // returns the message ts
  update: (channel: string, ts: string, text: string) => Promise<void>
  configDir: () => string                 // the account a new session runs on
  addDirs: () => Promise<string[]>        // the repos' main checkouts, so Read reaches dossiers and analysis files
  log: (m: string) => void
  /** Runs claude; calls `onLine` for each stdout line as it arrives; `timedOut` when it was killed at the timeout. */
  spawn?: (argv: string[], env: Record<string, string>, stdin: string, cwd: string, timeoutMs: number, onLine?: (line: string) => void) => Promise<SpawnResult>
  now?: () => number                      // the clock (tests)
}

async function bunSpawn(argv: string[], env: Record<string, string>, stdin: string, cwd: string, timeoutMs: number, onLine?: (line: string) => void): Promise<SpawnResult> {
  const p = Bun.spawn(argv, { cwd, env, stdin: new Blob([stdin]), stdout: "pipe", stderr: "pipe" })
  let timedOut = false
  const timer = setTimeout(() => { timedOut = true; p.kill() }, timeoutMs)
  const readOut = async () => {
    let all = "", buf = ""
    const dec = new TextDecoder()
    for await (const chunk of p.stdout as unknown as AsyncIterable<Uint8Array>) {
      const t = dec.decode(chunk, { stream: true })
      all += t; buf += t
      let k: number
      while ((k = buf.indexOf("\n")) >= 0) { const l = buf.slice(0, k); buf = buf.slice(k + 1); if (l) onLine?.(l) }
    }
    if (buf) onLine?.(buf)
    return all
  }
  const [stdout, stderr] = await Promise.all([readOut(), new Response(p.stderr).text()])
  const code = await p.exited
  clearTimeout(timer)
  return { stdout, stderr, code, timedOut }
}

const chains = new Map<string, Promise<void>>()

/**
 * Answer one DM message. `threadTs` is set for a reply in a thread: that thread's session resumes; otherwise the
 * message's own ts is the root of a new thread and a new session. Turns in one thread run one at a time; threads
 * run concurrently. The caller has already checked the sender is the owner (slack.ts `interpretEnvelope`).
 */
export function askAgent(m: { channel: string; ts: string; threadTs?: string; text: string }, deps: DmDeps): Promise<void> {
  const root = m.threadTs ?? m.ts
  const key = `${m.channel}:${root}`
  const prev = chains.get(key) ?? Promise.resolve()
  const next = prev.then(() => turn(key, root, m, deps)).catch((e) => deps.log(`dm agent: ${(e as Error).message}`))
  chains.set(key, next)
  void next.finally(() => { if (chains.get(key) === next) chains.delete(key) })
  return next
}

async function turn(key: string, root: string, m: { channel: string; text: string }, deps: DmDeps): Promise<void> {
  const store = readStore()
  const had = store[key]
  const dirs = await deps.addDirs()
  const s: DmSession = had ?? { sessionId: randomUUID(), configDir: deps.configDir(), started: new Date().toISOString(), last: "", turns: 0, cwd: pickCwd(m.text, dirs) }
  const cwd = s.cwd || pluginRoot()
  const placeholder = await deps.post(m.channel, root, had ? "_Looking into it (same conversation)…_" : "_Looking into it…_")
  const env: Record<string, string> = { ...(process.env as Record<string, string>), CLAUDE_CONFIG_DIR: s.configDir }
  delete env.CLAUDECODE
  const argv = claudeArgs({ sessionId: s.sessionId, resume: !!had, model: process.env.ASD_DM_AGENT_MODEL || DEFAULT_MODEL, addDirs: [join(home(), ".agentic"), ...dirs] })
  const timeout = Number(process.env.ASD_DM_AGENT_TIMEOUT_MS || DEFAULT_TIMEOUT_MS)
  const now = deps.now ?? Date.now
  const t0 = now()
  const prog = new Progress(t0)
  let lastUpdate = t0, shown = ""
  const tick = () => {
    const t = now()
    if (t - lastUpdate < PROGRESS_EVERY_MS) return
    const text = prog.status(t)
    lastUpdate = t
    if (text === shown) return
    shown = text
    deps.update(m.channel, placeholder, text).catch(() => {})
  }
  const timer = setInterval(tick, 5000)
  const run = (a: string[]) => (deps.spawn ?? bunSpawn)(a, env, m.text, cwd, timeout, (l) => { prog.feed(l); tick() })
  let r: SpawnResult, res: { ok: boolean; text: string }
  try {
    r = await run(argv)
    res = parseResult(r.stdout, r.stderr, r.code)
    if (had && !r.timedOut && !res.ok && /no conversation found/i.test(res.text)) {
      // The session never got written (its first turn failed early): start it again under the same id.
      r = await run(argv.map((a) => (a === "--resume" ? "--session-id" : a)))
      res = parseResult(r.stdout, r.stderr, r.code)
    }
  } finally { clearInterval(timer) }
  const secs = Math.round((now() - t0) / 1000)
  deps.log(`dm agent: ${had ? "resumed" : "new"} ${s.sessionId} cwd=${cwd} ${r.timedOut ? "timed out" : res.ok ? "answered" : "failed"} in ${secs} s`)
  if (res.ok || !had || r.timedOut) {
    // A failed first turn may still have created the session; keep it so a retry in the thread resumes it.
    const cur = readStore()
    cur[key] = { ...s, last: new Date().toISOString(), turns: s.turns + 1 }
    writeStore(cur)
  }
  const out = r.timedOut
    ? `The agent timed out after ${Math.round(timeout / 60_000)} min (${prog.turns} turns, last tool: ${prog.lastTool || "none"}). The conversation is kept: reply in this thread (e.g. "continue") to resume it.`
    : res.ok ? res.text : `The agent could not answer: ${res.text}`
  await deps.update(m.channel, placeholder, clipForSlack(out))
}
