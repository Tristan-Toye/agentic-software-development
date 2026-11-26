// PreToolUse hook for Bash: the no-polling rule of the building sub-agents (rules: shared/no-poll.ts). Applies only in
// a subagent (the hook input carries agent_id) whose type, with or without the plugin prefix, is in `no_poll_agents`
// of shared/context-guard.yaml; the orchestrator, the main session and any other agent are never touched.
// ASD_NO_POLL=0 turns it off. Denies with the documented PreToolUse JSON. Fails open.
import { configLoader } from "../config"
import { noPoll, noPollOn, shortAgent } from "../no-poll"
import { agentTypeOf, configPath, log, readInput, runHook } from "./common"

await runHook(async () => {
  if (!noPollOn()) return undefined
  const input = await readInput()
  if (!input?.tool_name || input.tool_name !== "Bash" || !input.agent_id) return undefined
  const cfg = configLoader(configPath())()
  const agent = shortAgent(agentTypeOf(input))
  if (!agent || !cfg.no_poll_agents.includes(agent)) return undefined
  const reason = noPoll(input.tool_name, input.tool_input ?? {}, cfg.no_poll_sleep_max)
  if (!reason) return undefined
  log("refused", input.session_id ?? "-", `no-poll ${agent} ${input.agent_id}: ${reason.slice(0, 160)}`)
  return { hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: `no polling: ${reason}` } }
})
