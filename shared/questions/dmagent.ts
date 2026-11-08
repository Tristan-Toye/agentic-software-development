// The owner's Slack DM agent: a message in the DM outside a question's thread goes to a Claude session that answers
// it with the runs MCP server (shared/claude/runs-mcp.ts). A new top-level message starts a new session; a reply in
// that message's thread resumes the same session. The prompt is references/dm-agent.md.
// Contract: references/owner-questions.md (Slack, "The DM agent").
import { randomUUID } from "node:crypto"
import { mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join } from "node:path"

export type DmSession = { sessionId: string; configDir: string; started: string; last: string; turns: number }
export type DmStore = Record<string, DmSession>   // key: "<channel>:<thread root ts>"

const home = (): string => process.env.HOME || homedir()
export const dmStoreFile = (): string => process.env.ASD_DM_AGENT_FILE || join(home(), ".agentic", "dm-agent.json")
export const pluginRoot = (): string => join(import.meta.dir, "..", "..")
export const promptFile = (): string => join(pluginRoot(), "references", "dm-agent.md")
const PLUGIN = "agentic-software-development"

export function readStore(f = dmStoreFile()): DmStore {
  try { return JSON.parse(readFileSync(f, "utf8")) ?? {} } catch { return {} }
}
export function writeStore(s: DmStore, f = dmStoreFile()): void {
  mkdirSync(dirname(f), { recursive: true })
  writeFileSync(f + ".tmp", JSON.stringify(s, null, 1))
  renameSync(f + ".tmp", f)
}

/** The read and act tools the agent may use; anything else is refused (`--permission-mode dontAsk`). */
export const ALLOWED_TOOLS = [
  `mcp__plugin_${PLUGIN}_runs`, `mcp__plugin_${PLUGIN}_nav`, "Read", "Grep", "Glob",
  // The context-mode plugin, where installed, cuts a long tool result and indexes the rest; its search reads it back.
  "mcp__plugin_context-mode_context-mode__ctx_search",
  "Bash(gh pr view:*)", "Bash(gh pr list:*)", "Bash(gh pr checks:*)", "Bash(gh run list:*)", "Bash(gh run view:*)",
  "Bash(git log:*)", "Bash(git status:*)", "Bash(git branch:*)", "Bash(git ls-remote:*)", "Bash(git show:*)",
]

/** The `claude` argv for one turn: a new session with its own id, or `--resume` of the thread's session. */
export function claudeArgs(o: { sessionId: string; resume: boolean; model: string; addDirs: string[] }): string[] {
  const bin = (process.env.ASD_CLAUDE_BIN || "claude").split(/\s+/).filter(Boolean)
  return [
    ...bin, "-p", "--output-format", "json", "--model", o.model,
    ...(o.resume ? ["--resume", o.sessionId] : ["--session-id", o.sessionId]),
    "--append-system-prompt-file", promptFile(),
    "--permission-mode", "dontAsk", "--allowedTools", ...ALLOWED_TOOLS,
    ...o.addDirs.flatMap((d) => ["--add-dir", d]),
  ]
}

/** The answer from `--output-format json`: `result`, or why there is none. */
export function parseResult(stdout: string, stderr: string, code: number | null): { ok: boolean; text: string } {
  let j: any = null
  try { j = JSON.parse(stdout.trim().split("\n").pop() ?? "") } catch { /* not json */ }
  if (j && typeof j.result === "string" && !j.is_error) return { ok: true, text: j.result.trim() || "(no answer)" }
  const why = (j?.result || j?.subtype || stderr || stdout || `exit ${code}`).toString().trim()
  return { ok: false, text: why.slice(0, 1500) }
}

/** Slack's message text limit is 40 000 characters; keep well under it. */
export const clipForSlack = (s: string, n = 38_000): string => (s.length > n ? s.slice(0, n - 40) + "\n…(cut; ask for the rest)" : s)

export type DmDeps = {
  post: (channel: string, threadTs: string, text: string) => Promise<string>   // returns the message ts
  update: (channel: string, ts: string, text: string) => Promise<void>
  configDir: () => string                 // the account a new session runs on
  addDirs: () => Promise<string[]>        // the repos' main checkouts, so Read reaches dossiers and analysis files
  log: (m: string) => void
  spawn?: (argv: string[], env: Record<string, string>, stdin: string, cwd: string, timeoutMs: number) => Promise<{ stdout: string; stderr: string; code: number | null }>
}

async function bunSpawn(argv: string[], env: Record<string, string>, stdin: string, cwd: string, timeoutMs: number) {
  const p = Bun.spawn(argv, { cwd, env, stdin: new Blob([stdin]), stdout: "pipe", stderr: "pipe" })
  const timer = setTimeout(() => p.kill(), timeoutMs)
  const [stdout, stderr] = await Promise.all([new Response(p.stdout).text(), new Response(p.stderr).text()])
  const code = await p.exited
  clearTimeout(timer)
  return { stdout, stderr, code }
}

const chains = new Map<string, Promise<void>>()

/**
 * Answer one DM message. `threadTs` is set for a reply in a thread: that thread's session resumes; otherwise the
 * message's own ts is the root of a new thread and a new session. Turns in one thread run one at a time.
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
  const s: DmSession = had ?? { sessionId: randomUUID(), configDir: deps.configDir(), started: new Date().toISOString(), last: "", turns: 0 }
  const placeholder = await deps.post(m.channel, root, had ? "_Looking into it (same conversation)…_" : "_Looking into it…_")
  const env: Record<string, string> = { ...(process.env as Record<string, string>), CLAUDE_CONFIG_DIR: s.configDir }
  delete env.CLAUDECODE
  const argv = claudeArgs({ sessionId: s.sessionId, resume: !!had, model: process.env.ASD_DM_AGENT_MODEL || "sonnet", addDirs: [join(home(), ".agentic"), ...(await deps.addDirs())] })
  const timeout = Number(process.env.ASD_DM_AGENT_TIMEOUT_MS || 600_000)
  const t0 = Date.now()
  let r = await (deps.spawn ?? bunSpawn)(argv, env, m.text, pluginRoot(), timeout)
  let res = parseResult(r.stdout, r.stderr, r.code)
  if (had && !res.ok && /no conversation found/i.test(res.text)) {
    // The session never got written (its first turn failed early): start it again under the same id.
    r = await (deps.spawn ?? bunSpawn)(argv.map((a) => (a === "--resume" ? "--session-id" : a)), env, m.text, pluginRoot(), timeout)
    res = parseResult(r.stdout, r.stderr, r.code)
  }
  deps.log(`dm agent: ${had ? "resumed" : "new"} ${s.sessionId} ${res.ok ? "answered" : "failed"} in ${Math.round((Date.now() - t0) / 1000)} s`)
  if (res.ok || !had) {
    // A failed first turn may still have created the session; keep it so a retry in the thread resumes it.
    const now = readStore()
    now[key] = { ...s, last: new Date().toISOString(), turns: s.turns + 1 }
    writeStore(now)
  }
  await deps.update(m.channel, placeholder, clipForSlack(res.ok ? res.text : `The agent could not answer: ${res.text}`))
}
