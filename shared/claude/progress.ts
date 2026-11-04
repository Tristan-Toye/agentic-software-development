// Automatic progress updates for the run's updates store (references/runner.md). Pure
// functions, so the hook test can call them without running the hook.
import { join } from "node:path"

// ---- Automatic progress updates (references/runner.md, "The updates store") ----
// The orchestrator's own tool calls already say what it is doing, so the hook turns the
// telling ones into `update` lines with `auto: true` — no model tokens. The run key is
// ASD_RUN_KEY under the runner; in an interactive run it is learnt from the first command
// that names a dossier (a dossier_edit.py, compose_payloads.py or ask_owner.py call) and
// remembered for the session.
const DOSSIER_PATH = /(\S*?)\/\.discovery\/dossiers\/([A-Z][A-Z0-9]*-\d+)[^\s'"]*\.md/

/** `<repo>.<ID>` for a dossier path: the repo is the main checkout's name, also from a worktree. */
const STUB_RECORD = /(\S*?)\/\.discovery\/analysis\/stub-(\d{4}-\d{2}-\d{2})(?:-([\w.-]+))?\.md/

function mainRepo(root: string): string | undefined {
  const common = Bun.spawnSync(["git", "-C", root, "rev-parse", "--path-format=absolute", "--git-common-dir"], { stdout: "pipe", stderr: "ignore" })
  const dir = common.exitCode === 0 ? common.stdout.toString().trim() : ""
  return (dir ? dir.replace(/\/\.git\/?$/, "") : root).split("/").filter(Boolean).pop()
}

/**
 * The stub run key for a /stub record file path in `text` (a command or a file path):
 * `<repo>.stub-<date>` for `stub-<date>.md`, `<repo>.<branch slug>` for
 * `stub-<date>-<branch slug>.md` — the dashboard's key, the branch with / as -.
 */
export function keyFromStubRecord(text: string, cwd: string): string | undefined {
  const m = text.match(STUB_RECORD)
  if (!m) return undefined
  const root = m[1] ? (m[1].startsWith("/") ? m[1] : join(cwd, m[1])) : cwd
  const repo = mainRepo(root)
  return repo ? `${repo}.${m[3] ?? `stub-${m[2]}`}` : undefined
}

export function keyFromCommand(cmd: string, cwd: string): string | undefined {
  const m = cmd.match(DOSSIER_PATH)
  let root: string | undefined, id: string | undefined
  if (m) { id = m[2]; root = m[1] ? (m[1].startsWith("/") ? m[1] : join(cwd, m[1])) : cwd }
  else {
    const d = cmd.match(/ask_owner\.py\s+(?:ask|note)\b.*?--dossier[ =]([A-Z][A-Z0-9]*-\d+)/)
    if (!d) return undefined
    id = d[1]
    const r = cmd.match(/--root[ =]("[^"]+"|'[^']+'|\S+)/)
    root = r ? r[1].replace(/^["']|["']$/g, "") : cwd
  }
  const common = Bun.spawnSync(["git", "-C", root, "rev-parse", "--path-format=absolute", "--git-common-dir"], { stdout: "pipe", stderr: "ignore" })
  const dir = common.exitCode === 0 ? common.stdout.toString().trim() : ""
  const main = dir ? dir.replace(/\/\.git\/?$/, "") : root
  const repo = main.split("/").filter(Boolean).pop()
  return repo && id ? `${repo}.${id}` : undefined
}

const textOf = (resp: any): string =>
  typeof resp === "string" ? resp : [resp?.stdout, resp?.output, resp?.content].find((v) => typeof v === "string") ?? ""
const lastMatch = (text: string, re: RegExp) => text.split("\n").map((l) => l.trim()).filter((l) => re.test(l)).pop()
const shortCmd = (s: string) => s.replace(/\s+/g, " ").trim().slice(0, 200)

/** The update a tool call is worth, or undefined. */
export function updateFor(tool: string, input: any, resp: any): string | undefined {
  if (tool === "Bash") {
    const cmd = String(input?.command ?? "")
    const out = textOf(resp)
    if (/\bdossier_edit\.py\s+log\b/.test(cmd)) {
      const q = cmd.match(/\blog\s+\S+\s+(?:'([^']*)'|"([^"]*)")/)
      return q ? `log: ${shortCmd(q[1] ?? q[2])}` : undefined
    }
    if (/\brun_tests\.py\b/.test(cmd)) {
      const s = lastMatch(out, /^passed \d+\s+failed \d+/)
      if (s) return `suite: ${s}`
      if (/\s--(?:help|print-cmd|selftest)\b|\s-h\b/.test(cmd)) return undefined
      // A detached run (`--detach`) or a background Bash call has not run yet: its `--wait` call reports the result.
      if (/\s--detach\b/.test(cmd) || /running in background|moved to the background/i.test(out) || (resp && typeof resp === "object" && (resp.backgroundTaskId || resp.background_task_id))) return "suite started"
      return "suite run finished"
    }
    if (/\brun_gates\.py\b/.test(cmd)) {
      const s = out.split("\n").map((l) => l.trim()).filter(Boolean).pop()
      return s ? `gates: ${shortCmd(s)}` : undefined
    }
    if (/\bgh\s+pr\s+create\b/.test(cmd)) {
      const u = out.match(/https:\/\/github\.com\/\S+\/pull\/\d+/)
      return u ? `PR opened: ${u[0]}` : undefined
    }
    if (/\bgh\s+pr\s+merge\b/.test(cmd)) return `PR merged: ${shortCmd(cmd.replace(/^.*gh\s+pr\s+merge\s*/, ""))}`
    return undefined
  }
  if (tool === "Workflow") {
    const name = String(input?.name ?? input?.scriptPath ?? "workflow").split("/").pop()
    const n = Array.isArray(input?.args) ? ` — ${input.args.length} agent(s)` : ""
    return `workflow started: ${name}${n}`
  }
  return undefined
}


/**
 * The spawn line for an Agent call: one line (agent type and description, never the
 * prompt) with a status. A background launch is `running` until SubagentStop closes it by
 * `agent_id`; a foreground call has already returned, so it is `done`.
 */
export function spawnFor(input: any, resp: any): { text: string; agent_id?: string; status: string } | undefined {
  if (!input) return undefined
  const type = String(input.subagent_type ?? "agent").replace(/^.*:/, "")
  const text = `${type}: ${shortCmd(String(input.description ?? ""))}`
  const raw = typeof resp === "string" ? resp : JSON.stringify(resp ?? "")
  const id = (typeof resp === "object" && resp && (resp.agentId || resp.agent_id)) || raw.match(/agentId[\\"]*:\s*[\\"]*([A-Za-z0-9_-]{6,})/)?.[1]
  const background = input.run_in_background === true || /launched successfully|running in the background/i.test(raw)
  return { text, agent_id: id ? String(id) : undefined, status: background ? "running" : "done" }
}

/** The status a subagent ends with, from its last message: a GAP or contract change stands out. */
export function endStatus(lastMessage: string): string {
  const m = String(lastMessage ?? "")
  if (/^\s*CONTRACT-CHANGE:/m.test(m)) return "contract-change"
  if (/^\s*GAP:/m.test(m)) return "gap"
  return "done"
}
