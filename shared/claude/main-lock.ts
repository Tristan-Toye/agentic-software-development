// PreToolUse hook: the main-checkout lock. Refuses a file edit or a mutating shell command in the main checkout of a
// repo that commits `.discovery/` (rules: shared/main-lock.ts). Fails open on unparseable input and on any error.
import { mainLock } from "../main-lock"
import { log, readInput, runHook } from "./common"

await runHook(async () => {
  if (process.env.ASD_MAIN_LOCK === "0") return undefined
  const input = await readInput()
  if (!input?.tool_name) return undefined
  const reason = mainLock(input.tool_name, input.tool_input ?? {}, input.cwd ?? process.cwd())
  if (!reason) return undefined
  log("refused", input.session_id ?? "-", `main lock ${input.tool_name}: ${reason.slice(0, 200)}`)
  return { hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason: reason } }
})
