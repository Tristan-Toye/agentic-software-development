// PreToolUse hook: the hard block of the owner's agent sessions (Slack DM agent, dashboard chat). Active only when
// ASD_AGENT_SURFACE is dm or chat (set by shared/questions/dmagent.ts) or ASD_HARD_BLOCK=1; in any other session it
// is a no-op. Applies to subagents too. Denies with the documented PreToolUse JSON. Fails open on unparseable input
// (logged). Rules: shared/hard-block.ts; contract: references/dm-agent.md ("The hard block").
import { homedir } from "node:os"
import { hardBlock, surfaceOf } from "../hard-block"
import { log, readInput, runHook } from "./common"

const deny = (reason: string) => ({
  hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: reason },
})

await runHook(async () => {
  const surface = surfaceOf(process.env)
  if (!surface) return undefined
  const input = await readInput()
  if (!input) { log("hard-block-unparseable", "-", `${surface}: hook input was not JSON; allowed`); return undefined }
  if (!input.tool_name) return undefined
  const reason = hardBlock(input.tool_name, input.tool_input ?? {}, { home: process.env.HOME || homedir(), cwd: input.cwd ?? process.cwd() })
  if (!reason) return undefined
  log("refused", input.session_id ?? "-", `hard block (${surface}) ${input.tool_name}: ${reason}`)
  return deny(`hard block: ${reason}`)
})
