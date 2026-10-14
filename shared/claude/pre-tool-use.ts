// PreToolUse hook for Read|Bash: the shared read guard, for the /work-on orchestrator only
// (the main session of a session that ran the work-on command; never a subagent).
// Denies with the documented PreToolUse JSON. Fails open.
import { configLoader } from "../config"
import { readGuard, type ReadGuardCall } from "../read-guard"
import { configPath, isOrchestrator, log, pipelineRoot, readInput, runHook } from "./common"

await runHook(async () => {
  const input = await readInput()
  if (!input || input.agent_id || !input.tool_name || !input.session_id) return undefined
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
  return {
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: `context-guard: ${reason}`,
    },
  }
})
