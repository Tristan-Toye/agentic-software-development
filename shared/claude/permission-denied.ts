// PermissionDenied hook: auto mode refused a tool call. Records it for the dashboard's Blocked card as one `blocked`
// update in the run's updates store (keyed like the PostToolUse hook: ASD_RUN_KEY under the runner, else the key the
// session learnt): the tool, the exact command (or path), the classifier's reason and the tool_use_id the owner's ruling
// refers to. Every refusal, with or without a run, is also appended to the one block log (`blockLogFile`,
// ~/.agentic/blocks.jsonl) that `scripts/block_report.py` reads, to see which refusals recur and deserve a rule.
// Observe only: it never retries or approves, and never fails.
import { appendFileSync, mkdirSync } from "node:fs"
import { homedir } from "node:os"
import { dirname, join } from "node:path"
import { loadState, readInput, runHook } from "./common"

/** The log of every refusal and every owner ruling on one, across runs and repos (ASD_BLOCK_LOG overrides). */
export const blockLogFile = (): string => process.env.ASD_BLOCK_LOG || join(process.env.HOME || homedir(), ".agentic", "blocks.jsonl")

/** Append one line to the block log; never throws. */
export function logBlock(line: Record<string, unknown>): void {
  try {
    const f = blockLogFile()
    mkdirSync(dirname(f), { recursive: true })
    appendFileSync(f, JSON.stringify(line) + "\n")
  } catch {
    // the log is for later reading; never fail what it records
  }
}

/** What was refused, in one string: a Bash command verbatim, a file path, else the input as JSON. At most 2000 chars. */
export function blockedTarget(tool: string, input: any): string {
  const s = tool === "Bash" || tool === "PowerShell" ? String(input?.command ?? "")
    : typeof input?.file_path === "string" ? input.file_path
    : typeof input?.url === "string" ? input.url
    : JSON.stringify(input ?? {})
  return s.length > 2000 ? s.slice(0, 2000) + " [cut]" : s
}

if (import.meta.main) await runHook(async () => {
  try {
    const input: any = await readInput()
    if (!input || !input.session_id) return undefined
    const key: string | undefined = process.env.ASD_RUN_KEY || (loadState(input.session_id) as any).runKey
    const tool = String(input.tool_name ?? "")
    const target = blockedTarget(tool, input.tool_input)
    logBlock({
      ts: new Date().toISOString().replace(/\.\d+Z$/, "Z"), event: "blocked", run: key || null, cwd: String(input.cwd ?? ""),
      session_id: String(input.session_id), tool, target, reason: String(input.reason ?? "").slice(0, 600),
      tool_use_id: String(input.tool_use_id ?? ""), ...(input.agent_id ? { agent_id: String(input.agent_id) } : {}),
    })
    if (process.env.ASD_RUN_UPDATES === "0" || !key || /[\\/]/.test(key)) return undefined
    const dir = process.env.ASD_RUNS_DIR || join(homedir(), ".agentic", "runs")
    mkdirSync(dir, { recursive: true })
    const line = {
      ts: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
      kind: "update",
      auto: true,
      event: "blocked",
      tool,
      target,
      reason: String(input.reason ?? "").slice(0, 600),
      tool_use_id: String(input.tool_use_id ?? ""),
      ...(input.agent_id ? { agent_id: String(input.agent_id) } : {}),
      text: `blocked: ${tool} ${target.split("\n")[0].slice(0, 200)}`,
    }
    appendFileSync(join(dir, `${key}.jsonl`), JSON.stringify(line) + "\n")
  } catch {
    // never fail the hook
  }
  return undefined
})
