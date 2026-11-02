// PostToolUse hook for the /work-on orchestrator. Counts its tool calls since its last
// phase_checkpoint and, every NUDGE_EVERY calls, adds the shared reminder to the context.
// Also watches the context: past PHASE_CHECKPOINT_PRESSURE_RATIO of the session's
// autoCompactWindow it asks once for a checkpoint with a NEXT: line, so the restore after
// the coming compaction starts from minutes-old state. A phase_checkpoint call resets the
// count. Fails open.
import { appendFileSync, mkdirSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"
import { nudgeEvery, reminderText } from "../checkpoint"
import {
  compactWindow,
  contextTokens,
  isCheckpointTool,
  isOrchestrator,
  loadState,
  log,
  pressureRatio,
  pressureText,
  readInput,
  runHook,
  saveState,
} from "./common"

const out = (text: string) => ({ hookSpecificOutput: { hookEventName: "PostToolUse", additionalContext: text } })

// Under the runner (ASD_RUN_KEY set) a phase_checkpoint call is also a `stage` line in the
// run's updates store, $ASD_RUNS_DIR/<key>.jsonl (references/runner.md). One O_APPEND write;
// a failure never reaches the hook.
function recordStage(input: { tool_input?: any }) {
  try {
    const key = process.env.ASD_RUN_KEY
    if (!key || /[\\/]/.test(key)) return
    const dir = process.env.ASD_RUNS_DIR || join(homedir(), ".agentic", "runs")
    mkdirSync(dir, { recursive: true })
    const clip = (t: string) => (t.length > 3500 ? t.slice(0, 3500) + " [cut]" : t)
    const line = {
      ts: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
      kind: "stage",
      stage: clip(String(input.tool_input?.phase ?? "")),
      text: clip(String(input.tool_input?.summary ?? "").split("\n")[0]),
    }
    appendFileSync(join(dir, `${key}.jsonl`), JSON.stringify(line) + "\n")
  } catch {
    // never fail the hook
  }
}

await runHook(async () => {
  const input = await readInput()
  if (!input || input.agent_id || !input.session_id || !input.tool_name) return undefined
  if (isCheckpointTool(input.tool_name)) recordStage(input)
  if (!isOrchestrator(input)) return undefined
  const state = loadState(input.session_id)
  if (isCheckpointTool(input.tool_name)) {
    saveState(input.session_id, { ...state, calls: 0 })
    log("recorded", input.session_id, `${String(input.tool_input?.phase ?? "").slice(0, 80)}`)
    return undefined
  }
  const n = (state.calls ?? 0) + 1
  const next = { ...state, calls: n }

  // Context pressure: once per compaction cycle; the flag clears when the context falls back.
  const ratio = pressureRatio()
  const window = ratio > 0 ? compactWindow(input.cwd) : undefined
  const tokens = window && input.transcript_path ? contextTokens(input.transcript_path) : undefined
  if (window && tokens !== undefined) {
    const over = tokens >= ratio * window
    if (!over) next.pressureNudged = false
    else if (!state.pressureNudged) {
      next.pressureNudged = true
      saveState(input.session_id, next)
      log("nudge", input.session_id, `context ${tokens} of window ${window}`)
      return out(pressureText(tokens, window))
    }
  }

  saveState(input.session_id, next)
  const every = nudgeEvery()
  if (every === 0 || n % every !== 0) return undefined
  log("nudge", input.session_id, `${n} tool calls since the last checkpoint`)
  return out(reminderText(n))
})
