// PreToolUse hook for Read|Bash. In the /work-on orchestrator (the main session of a
// session that ran the work-on command) it applies the shared read guard. In a subagent it
// applies only the unit test author's read boundary (shared/blind-read.ts). Denies with the
// documented PreToolUse JSON. Fails open.
import { BLIND_AGENT, blindRead } from "../blind-read"
import { configLoader } from "../config"
import { bigGuardOn, phaseFileOf, phaseRereadReason, readGuard, windowReason, type ReadGuardCall } from "../read-guard"
import { agentTypeOf, configPath, isOrchestrator, loadState, log, pipelineRoot, readInput, runHook, saveState } from "./common"

const deny = (reason: string) => ({
  hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: reason },
})

await runHook(async () => {
  const input = await readInput()
  if (!input || !input.tool_name || !input.session_id) return undefined
  if (input.agent_id) {
    if (input.tool_name !== "Read" || agentTypeOf(input) !== BLIND_AGENT) return undefined
    const reason = blindRead(String(input.tool_input?.file_path ?? ""), input.cwd ?? process.cwd())
    if (!reason) return undefined
    log("refused", input.session_id, `${BLIND_AGENT} ${input.agent_id} Read: ${reason}`)
    return deny(`blind read: ${reason}`)
  }
  const toolInput = input.tool_input ?? {}
  let call: ReadGuardCall
  if (input.tool_name === "Read") {
    call = {
      kind: "read",
      path: String(toolInput.file_path ?? ""),
      offset: toolInput.offset,
      limit: toolInput.limit,
    }
  } else if (input.tool_name === "Bash") call = { kind: "bash", command: String(toolInput.command ?? "") }
  else return undefined
  if (!isOrchestrator(input)) return undefined
  const cfg = configLoader(configPath())()
  const cwd = input.cwd ?? process.cwd()
  let reason = readGuard(call, cwd, cfg, pipelineRoot())
  if (!reason && call.kind === "read" && bigGuardOn()) reason = statefulRead(input.session_id, call, cwd)
  if (!reason) return undefined
  log("refused", input.session_id, `work-on ${input.tool_name}: ${reason}`)
  return deny(`context-guard: ${reason}`)
})

// The refusals that need memory: a 4th window on one file, a second whole read of a phase file.
// Counts live in the session state and are reset by a compaction (session-start.ts); a refused
// call repeated verbatim goes through, so a file the nav tools cannot parse is never stuck.
function statefulRead(session: string, call: Extract<ReadGuardCall, { kind: "read" }>, cwd: string): string | undefined {
  const file = call.path
  if (!file) return undefined
  const state = loadState(session)
  const key = `${file}:${call.offset ?? ""}:${call.limit ?? ""}`
  if (state.lastRefused === key) {
    saveState(session, { ...state, lastRefused: undefined })
    return undefined
  }
  const refuse = (why: string) => {
    saveState(session, { ...state, lastRefused: key })
    return why
  }
  const windowed = call.limit !== undefined || (call.offset !== undefined && call.offset > 1)
  if (windowed) {
    const prior = state.windows?.[file] ?? 0
    const why = windowReason(file, call.limit, prior)
    if (why) return refuse(why)
    saveState(session, { ...state, windows: { ...state.windows, [file]: prior + 1 } })
    return undefined
  }
  const phase = phaseFileOf(file)
  if (phase) {
    const read = state.phaseReads ?? []
    if (read.includes(phase)) return refuse(phaseRereadReason(phase))
    saveState(session, { ...state, phaseReads: [...read, phase] })
  }
  return undefined
}
