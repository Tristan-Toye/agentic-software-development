import type { Plugin } from "@opencode-ai/plugin"
import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

// The file is symlinked into ~/.config/opencode/plugins and runs from its real path in
// this repo, which has no node_modules: take the plugin SDK (and its zod) from
// opencode's config directory, where opencode installs it.
const configDir = process.env.OPENCODE_CONFIG_DIR ?? join(homedir(), ".config", "opencode")
const { tool } = (await import(
  Bun.resolveSync("@opencode-ai/plugin", configDir)
)) as typeof import("@opencode-ai/plugin")

// Phase checkpoint — resets an orchestrator's context at a phase boundary it chooses.
//
// A /work-on run is ~700 model steps, and the provider bills every cached token on
// every step: a context that grows to 400k is paid for hundreds of times. opencode's
// own compaction is token-triggered, so it fires mid-investigation; DCP's `compress`
// tool is model-triggered at any time. This plugin gives the orchestrator one tool,
// `phase_checkpoint`, to call when a phase is done and its outcome is in the dossier's
// `## Build log`. From the next request on, everything before that call is dropped
// from what the model sees, and the phase summaries ride on the original task
// message instead.
//
// Only the outgoing request changes (experimental.chat.messages.transform). The
// session's stored history, the TUI transcript and /work-on's resume from the dossier
// are untouched. The cut costs one prompt-cache miss at the boundary; the next phase
// then caches a small prefix.
//
// Checkpoints persist per session under $XDG_DATA_HOME/opencode/phase-checkpoint/
// (default ~/.local/share/...), so a restarted opencode keeps the reset.
//
// The tool is permission-denied globally in opencode.jsonc and allowed only in
// work-on's front matter; AGENTS below is the second guard.
//
// OPENCODE_PHASE_CHECKPOINT_DEBUG=1 logs cuts to stderr.

const AGENTS = new Set(["work-on"])
const MAX_SUMMARY_CHARS = 6000
const TOOL = "phase_checkpoint"
const debug = process.env.OPENCODE_PHASE_CHECKPOINT_DEBUG === "1"
const log = (...args: unknown[]) => debug && console.error("[phase-checkpoint]", ...args)

type Checkpoint = { phase: string; summary: string; messageID: string; marker: string; at: string }

const PHASE_RE = /^Phase (\d+)/
const PHASE_FILE_RE = /references\/work-on\/phase-(\d+)\.md$/
// sessionID -> the number of the last phase file the session read
const phaseReads = new Map<string, number>()

function phaseNumber(phase: string): number | undefined {
  const match = PHASE_RE.exec(phase.trim())
  return match ? Number(match[1]) : undefined
}

const dir = join(process.env.XDG_DATA_HOME ?? join(homedir(), ".local", "share"), "opencode", "phase-checkpoint")
const sessions = new Map<string, Checkpoint[]>()

function load(sessionID: string): Checkpoint[] {
  let list = sessions.get(sessionID)
  if (list) return list
  try {
    list = JSON.parse(readFileSync(join(dir, `${sessionID}.json`), "utf8")) as Checkpoint[]
  } catch {
    list = []
  }
  sessions.set(sessionID, list)
  return list
}

function save(sessionID: string, list: Checkpoint[]) {
  sessions.set(sessionID, list)
  try {
    mkdirSync(dir, { recursive: true })
    writeFileSync(join(dir, `${sessionID}.json`), JSON.stringify(list, null, 2))
  } catch (err) {
    log("could not persist checkpoint:", err)
  }
}

type Entry = { info: { id: string; sessionID: string; role: string }; parts: any[] }

// The index of the message that made the latest checkpoint call: by message ID first,
// then by the marker in the tool's own output (another plugin may rename messages).
function cutIndex(messages: Entry[], cp: Checkpoint): number {
  const byId = messages.findIndex((m) => m.info.id === cp.messageID)
  if (byId !== -1) return byId
  return messages.findIndex((m) =>
    m.parts.some((p) => p?.type === "tool" && p.tool === TOOL && String(p.state?.output ?? "").includes(cp.marker)),
  )
}

function summaryText(list: Checkpoint[]): string {
  const phases = list.map((cp) => `### ${cp.phase}\n\n${cp.summary.trim()}`).join("\n\n")
  return (
    "## Completed phases (phase_checkpoint)\n\n" +
    "Earlier steps of this run are no longer in context. What they established is below " +
    "and in the dossier's `## Build log`, which is the record. Continue with the next phase: " +
    "read its phase file and the dossier sections it needs; do not redo finished phases.\n\n" +
    phases
  )
}

// Applies the latest checkpoint to an outgoing message list, in place. Not exported:
// opencode may call every export of a plugin file as a plugin.
function applyCheckpoint(messages: Entry[], list: Checkpoint[]): boolean {
  if (messages.length < 2 || list.length === 0) return false
  const cut = cutIndex(messages, list[list.length - 1])
  if (cut <= 1) return false
  const first = messages[0]
  if (first.info.role !== "user") return false
  const part = {
    id: `${first.info.id}-phase-checkpoint`,
    sessionID: first.info.sessionID,
    messageID: first.info.id,
    type: "text",
    text: summaryText(list),
    synthetic: true,
  }
  // New objects, never the stored ones: only this request changes.
  messages[0] = { info: first.info, parts: [...first.parts, part] }
  messages.splice(1, cut - 1)
  return true
}

export const PhaseCheckpointPlugin: Plugin = async () => ({
  tool: {
    [TOOL]: tool({
      description:
        "End a /work-on phase: drop every earlier step of this run from your context from the next " +
        "request on, keeping the original task and the summaries of completed phases. Call it ONLY at a phase " +
        "boundary, after the phase's outcome is in the dossier's ## Build log — never mid-investigation, never " +
        "to save space. The summary is what you will know about all earlier work: the dossier path and worktree, " +
        "the phase just finished and the next one, commits and SHAs, rulings and open items, and anything the " +
        `next phase must not rediscover. At most ${MAX_SUMMARY_CHARS} characters; detail belongs in the build log.`,
      args: {
        phase: tool.schema.string().describe('the phase just finished, starting "Phase <N>", e.g. "Phase 3 — Mechanical check"'),
        summary: tool.schema.string().describe("the hand-off to the rest of the run, self-contained"),
      },
      async execute({ phase, summary }, ctx) {
        if (!AGENTS.has(ctx.agent)) return `${TOOL}: refused — only ${[...AGENTS].join(" and ")} reset their context`
        const num = phaseNumber(phase)
        if (num === undefined) return `${TOOL}: refused — name the phase as "Phase <N> — <title>"`
        const current = phaseReads.get(ctx.sessionID)
        if (current !== undefined && num !== current)
          return `${TOOL}: refused — the phase file in use is Phase ${current}, not Phase ${num}. Check the phase before you cut: a wrong boundary drops the steps you are still working from.`
        if (summary.length > MAX_SUMMARY_CHARS)
          return `${TOOL}: refused — the summary is ${summary.length} characters, the limit is ${MAX_SUMMARY_CHARS}. Put the detail in ## Build log and point at it.`
        const list = load(ctx.sessionID)
        const marker = `checkpoint ${list.length + 1}`
        save(ctx.sessionID, [
          ...list,
          { phase, summary, messageID: ctx.messageID, marker, at: new Date().toISOString() },
        ])
        return `${TOOL}: ${marker} recorded after ${phase}. From the next request on, earlier steps are out of context; the summaries of ${list.length + 1} completed phase(s) stay.`
      },
    }),
  },

  // A phase file opened while the previous phase has no checkpoint is a missed
  // boundary: say so in that read's result, where the orchestrator looks next.
  "tool.execute.after": async (input, output) => {
    if (input.tool !== "read") return
    const match = PHASE_FILE_RE.exec(String(input.args?.filePath ?? ""))
    if (!match) return
    const num = Number(match[1])
    const previous = phaseReads.get(input.sessionID)
    phaseReads.set(input.sessionID, num)
    if (previous === undefined || num <= previous) return
    if (load(input.sessionID).some((cp) => phaseNumber(cp.phase) === previous)) return
    output.output +=
      `\n\n[${TOOL}] Phase ${previous} has no checkpoint. When its outcome is in ## Build log, call ` +
      `${TOOL} for Phase ${previous} before the first step of Phase ${num}.`
  },

  "experimental.chat.messages.transform": async (_input, output) => {
    const messages = output.messages as unknown as Entry[]
    const sessionID = messages[0]?.info.sessionID
    if (!sessionID) return
    const list = load(sessionID)
    if (list.length === 0) return
    const before = messages.length
    if (applyCheckpoint(messages, list)) log(`${sessionID}: ${before} -> ${messages.length} messages`)
  },
})
