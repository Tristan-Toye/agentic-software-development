// PreToolUse hook for Read|Edit|Write|Grep|Bash. In the /work-on orchestrator (the main session of a
// session that ran the work-on command) it applies the shared read guard. In a subagent it
// applies only the unit test author's read and edit boundary, the integration test author's seal and
// both authors' nav limit (code_outline, code_item in contract mode only) (shared/blind-read.ts). Denies with the
// documented PreToolUse JSON. Fails open.
import { BLIND_AGENT, INTEGRATION_AGENT, blindRead, isNavTool, navBodyGuard, sealedGrep, sealedRead } from "../blind-read"
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
    const agent = agentTypeOf(input)
    const ti = input.tool_input ?? {}
    const cwd = input.cwd ?? process.cwd()
    if ((agent === BLIND_AGENT || agent === INTEGRATION_AGENT) && isNavTool(input.tool_name)) {
      // The contract-lookup tools: code_outline and code_item in contract mode only, never a body.
      const reason = navBodyGuard(input.tool_name, ti)
      if (!reason) return undefined
      log("refused", input.session_id, `${agent} ${input.agent_id} ${input.tool_name}: ${reason}`)
      return deny(`nav bodies: ${reason}`)
    }
    if (agent === BLIND_AGENT && (input.tool_name === "Read" || input.tool_name === "Edit")) {
      // Edit (the in-place change of an existing test file, #392) stays inside the same boundary as Read.
      const reason = blindRead(String(ti.file_path ?? ""), cwd)
      if (!reason) return undefined
      log("refused", input.session_id, `${BLIND_AGENT} ${input.agent_id} ${input.tool_name}: ${reason}`)
      return deny(`blind ${input.tool_name.toLowerCase()}: ${reason}`)
    }
    if (agent === INTEGRATION_AGENT) {
      const reason =
        input.tool_name === "Read" || input.tool_name === "Edit" || input.tool_name === "Write"
          ? sealedRead(String(ti.file_path ?? ""), cwd)
          : input.tool_name === "Grep"
            ? sealedGrep(ti.path === undefined ? undefined : String(ti.path), ti.output_mode, cwd)
            : undefined
      if (!reason) return undefined
      log("refused", input.session_id, `${INTEGRATION_AGENT} ${input.agent_id} ${input.tool_name}: ${reason}`)
      return deny(`sealed: ${reason}`)
    }
    return undefined
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
