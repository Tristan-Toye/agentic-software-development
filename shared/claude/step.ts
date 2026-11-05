// PreToolUse hook (Bash|Workflow|Skill): the step a pipeline orchestrator is about to take, as a short `step` line
// in the run's updates store, for the owner's timeline ("Spawning 4 agents", "Running tests: …"). The dashboard
// shows it; Slack does not post it. Same run key as the PostToolUse progress lines; ASD_RUN_UPDATES=0 turns it off.
// Never in a sub-agent. Fails open, never decides anything.
import { appendFileSync, mkdirSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import { isPipelineSession, loadState, readInput, runHook } from "./common"
import { stepFor } from "./progress"

await runHook(async () => {
  const input = await readInput()
  if (!input || input.agent_id || !input.session_id || !input.tool_name) return undefined
  if (process.env.ASD_RUN_UPDATES === "0") return undefined
  try {
    const key = process.env.ASD_RUN_KEY || ((isPipelineSession(input) ? (loadState(input.session_id) as any).runKey : undefined) as string | undefined)
    if (!key || /[\\/]/.test(key)) return undefined
    const text = stepFor(input.tool_name, input.tool_input)
    if (!text) return undefined
    const dir = process.env.ASD_RUNS_DIR || join(homedir(), ".agentic", "runs")
    mkdirSync(dir, { recursive: true })
    const line = { ts: new Date().toISOString().replace(/\.\d+Z$/, "Z"), kind: "update", auto: true, event: "step", text: text.slice(0, 3500) }
    appendFileSync(join(dir, `${key}.jsonl`), JSON.stringify(line) + "\n")
  } catch { /* never fail the hook */ }
  return undefined
})
