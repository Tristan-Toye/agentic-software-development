// PostToolUse hook: counts the /work-on orchestrator's tool calls since its last
// phase_checkpoint and, every NUDGE_EVERY calls, adds the shared reminder to the context.
// A phase_checkpoint call resets the count. Fails open.
import { nudgeEvery, reminderText } from "../checkpoint"
import { isCheckpointTool, isOrchestrator, loadState, log, readInput, runHook, saveState } from "./common"

await runHook(async () => {
  const input = await readInput()
  if (!input || input.agent_id || !input.session_id || !input.tool_name) return undefined
  const every = nudgeEvery()
  if (every === 0 || !isOrchestrator(input)) return undefined
  const state = loadState(input.session_id)
  if (isCheckpointTool(input.tool_name)) {
    saveState(input.session_id, { ...state, calls: 0 })
    log("recorded", input.session_id, `${String(input.tool_input?.phase ?? "").slice(0, 80)}`)
    return undefined
  }
  const n = (state.calls ?? 0) + 1
  saveState(input.session_id, { ...state, calls: n })
  if (n % every !== 0) return undefined
  log("nudge", input.session_id, `${n} tool calls since the last checkpoint`)
  return { hookSpecificOutput: { hookEventName: "PostToolUse", additionalContext: reminderText(n) } }
})
