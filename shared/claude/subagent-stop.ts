// SubagentStop hook: closes the dashboard's spawn line for a finished subagent. It appends a
// `spawn-done` update (agent_id, status done | gap | contract-change) to the run's updates
// store, keyed like the PostToolUse hook: ASD_RUN_KEY under the runner, else the key the
// session learnt. Never blocks the subagent and never fails.
import { appendFileSync, mkdirSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import { loadState, readInput, runHook } from "./common"
import { endStatus } from "./progress"

await runHook(async () => {
  try {
    const input: any = await readInput()
    if (!input || !input.agent_id || !input.session_id || process.env.ASD_RUN_UPDATES === "0") return undefined
    const key: string | undefined = process.env.ASD_RUN_KEY || (loadState(input.session_id) as any).runKey
    if (!key || /[\\/]/.test(key)) return undefined
    const dir = process.env.ASD_RUNS_DIR || join(homedir(), ".agentic", "runs")
    mkdirSync(dir, { recursive: true })
    const line = {
      ts: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
      kind: "update",
      auto: true,
      event: "spawn-done",
      agent_id: String(input.agent_id),
      status: endStatus(String(input.last_assistant_message ?? "")),
      text: `${String(input.agent_type ?? "agent").replace(/^.*:/, "")} finished`,
    }
    appendFileSync(join(dir, `${key}.jsonl`), JSON.stringify(line) + "\n")
  } catch {
    // never fail the hook
  }
  return undefined
})
