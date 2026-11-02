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
      return s ? `suite: ${s}` : "suite run finished"
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
  if (tool === "Agent") return `spawned ${String(input?.subagent_type ?? "agent").replace(/^.*:/, "")}: ${shortCmd(String(input?.description ?? ""))}`
  return undefined
}

