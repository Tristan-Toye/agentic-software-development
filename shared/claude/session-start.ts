// SessionStart hook (matcher `compact`): after a compaction, restores what the
// orchestrator's phase_checkpoint calls recorded — the original task and the summaries of
// the completed phases and stages — read back from the session transcript, which holds
// every call. It also restores the mid-stage state a compaction would otherwise lose: every
// Agent/Task spawn and Workflow launch since the last checkpoint with its agent or run ID
// and whether its result arrived, and the tail of the dossier's `## Build log`. Fails open.
import { summaryText } from "../checkpoint"
import { buildLogTail, log, readCheckpoints, readInFlight, readInput, runHook } from "./common"
import type { InFlight } from "./common"

const MAX_TASK_CHARS = 4000
const MAX_EXTRA_CHARS = 6000 // in-flight state plus the build-log tail
const MAX_IN_FLIGHT_CHARS = 2400
const BUILD_LOG_LINES = 40

const describe = (i: InFlight) =>
  `- ${i.kind} "${i.label}"${i.type ? ` (${i.type})` : ""}` +
  [i.agentId && `agent ${i.agentId}`, i.runId && `run ${i.runId}`, i.taskId && `task ${i.taskId}`].filter(Boolean).map((s) => ` — ${s}`).join("") +
  ` — ${i.arrived ? "result arrived" : "no result seen yet"}`

function inFlightText(items: InFlight[]): string {
  if (items.length === 0) return ""
  let out = `## In flight since the last checkpoint (${items.length})\n\n`
  out += "A spawn with no result seen may still be running: check it (/workflows, the task list) before you re-spawn it. Resume a finished agent with SendMessage to its agent ID.\n\n"
  const rows: string[] = []
  let used = out.length
  for (const item of items) {
    const row = describe(item)
    if (used + row.length + 1 > MAX_IN_FLIGHT_CHARS) {
      rows.push(`- … ${items.length - rows.length} more (see ## Build log)`)
      break
    }
    rows.push(row)
    used += row.length + 1
  }
  return out + rows.join("\n") + "\n\n"
}

await runHook(async () => {
  const input = await readInput()
  if (!input || input.agent_id || !input.transcript_path) return undefined
  if (input.source !== undefined && input.source !== "compact") return undefined
  const { task, checkpoints } = await readCheckpoints(input.transcript_path)
  let extra = ""
  let flight: Awaited<ReturnType<typeof readInFlight>> | undefined
  try {
    flight = await readInFlight(input.transcript_path)
    extra = inFlightText(flight.items)
    const tail = buildLogTail(flight.dossier, flight.args, input.cwd, BUILD_LOG_LINES)
    if (tail && (flight.items.length > 0 || checkpoints.length > 0)) {
      const head = `## Build log, last lines (${tail.path})\n\n`
      const room = MAX_EXTRA_CHARS - extra.length - head.length
      if (room > 200) {
        // keep the newest lines: drop from the top until the tail fits
        let text = tail.text
        while (text.length > room && text.includes("\n")) text = text.slice(text.indexOf("\n") + 1)
        extra += head + (text.length > room ? text.slice(-room) : text) + "\n\n"
      }
    }
  } catch {
    // the in-flight part is optional
  }
  if (checkpoints.length === 0 && (flight?.items.length ?? 0) === 0) return undefined
  const original = task.length > MAX_TASK_CHARS ? task.slice(0, MAX_TASK_CHARS) + " …" : task
  const last = checkpoints.length > 0 ? checkpoints[checkpoints.length - 1].phase.slice(0, 80) : "-"
  log("restored", input.session_id ?? "-", `${checkpoints.length} checkpoint(s), last "${last}", ${flight?.items.length ?? 0} in flight, ${extra.length} extra chars`)
  return {
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext:
        (original ? `## Original task\n\n${original}\n\n` : "") + (checkpoints.length > 0 ? summaryText(checkpoints) : "") + (extra ? "\n\n" + extra.trimEnd() : ""),
    },
  }
})
