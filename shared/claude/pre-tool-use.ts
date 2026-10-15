// PreToolUse hook for Read|Bash. In the /work-on orchestrator (the main session of a
// session that ran the work-on command) it applies the shared read guard. In a subagent it
// applies only the unit test author's read boundary (shared/blind-read.ts). Denies with the
// documented PreToolUse JSON. Fails open.
import { BLIND_AGENT, blindRead } from "../blind-read"
import { configLoader } from "../config"
import { readGuard, type ReadGuardCall } from "../read-guard"
import { agentTypeOf, configPath, isOrchestrator, log, pipelineRoot, readInput, runHook } from "./common"

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
  const reason = readGuard(call, input.cwd ?? process.cwd(), configLoader(configPath())(), pipelineRoot())
  if (!reason) return undefined
  log("refused", input.session_id, `work-on ${input.tool_name}: ${reason}`)
  return deny(`context-guard: ${reason}`)
})
