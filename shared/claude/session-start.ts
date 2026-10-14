// SessionStart hook (matcher `compact`): after a compaction, restores what the
// orchestrator's phase_checkpoint calls recorded — the original task and the summaries of
// the completed phases and stages — read back from the session transcript, which holds
// every call. Fails open.
import { summaryText } from "../checkpoint"
import { log, readCheckpoints, readInput, runHook } from "./common"

const MAX_TASK_CHARS = 4000

await runHook(async () => {
  const input = await readInput()
  if (!input || input.agent_id || !input.transcript_path) return undefined
  if (input.source !== undefined && input.source !== "compact") return undefined
  const { task, checkpoints } = await readCheckpoints(input.transcript_path)
  if (checkpoints.length === 0) return undefined
  const original = task.length > MAX_TASK_CHARS ? task.slice(0, MAX_TASK_CHARS) + " …" : task
  log("restored", input.session_id ?? "-", `${checkpoints.length} checkpoint(s), last "${checkpoints[checkpoints.length - 1].phase.slice(0, 80)}"`)
  return {
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext: (original ? `## Original task\n\n${original}\n\n` : "") + summaryText(checkpoints),
    },
  }
})
