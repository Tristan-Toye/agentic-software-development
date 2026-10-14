// What both hosts share about phase_checkpoint: the record's shape, the phase-name rule,
// the limits, and the texts the model reads (the reminder, and the summary of completed
// phases that rides on the original task after a cut or a compaction).

export const TOOL = "phase_checkpoint"
export const MAX_SUMMARY_CHARS = 6000

// phase: what just finished; messageID and marker locate the cut on opencode (a Claude
// Code checkpoint is found in the transcript instead, so they stay empty there).
export type Checkpoint = { phase: string; summary: string; messageID: string; marker: string; at: string }

const PHASE_RE = /^Phase (\d+)/

// The number of "Phase <N> — <stage>", or undefined when the name does not start so.
export function phaseNumber(phase: string): number | undefined {
  const match = PHASE_RE.exec(phase.trim())
  return match ? Number(match[1]) : undefined
}

// Reminder after every NUDGE_EVERY tool calls without a checkpoint; 0 = off. The first
// variable that is set wins (the opencode one predates the shared module).
export function nudgeEvery(env: Record<string, string | undefined> = process.env): number {
  const n = Number(env.PHASE_CHECKPOINT_NUDGE_CALLS ?? env.OPENCODE_PHASE_CHECKPOINT_NUDGE_CALLS ?? 80)
  return Number.isFinite(n) && n >= 0 ? Math.floor(n) : 80
}

export function reminderText(calls: number): string {
  return (
    `[${TOOL}] ${calls} tool calls since the last checkpoint. When the stage you are in is done and its ` +
    `outcome is in ## Build log, call ${TOOL} with "Phase <N> — <stage>" before starting the next one.`
  )
}

// The refusals the tool returns for a bad call, or undefined when the argument passes.
export function validatePhase(phase: string): string | undefined {
  return phaseNumber(phase) === undefined ? 'name the phase as "Phase <N> — <title>"' : undefined
}
export function validateSummary(summary: string): string | undefined {
  return summary.length > MAX_SUMMARY_CHARS
    ? `the summary is ${summary.length} characters, the limit is ${MAX_SUMMARY_CHARS}. Put the detail in ## Build log and point at it.`
    : undefined
}
export const validateCheckpoint = (phase: string, summary: string) => validatePhase(phase) ?? validateSummary(summary)

export function summaryText(list: Pick<Checkpoint, "phase" | "summary">[]): string {
  const phases = list.map((cp) => `### ${cp.phase}\n\n${cp.summary.trim()}`).join("\n\n")
  return (
    "## Completed phases (phase_checkpoint)\n\n" +
    "Earlier steps of this run are no longer in context. What they established is below " +
    "and in the dossier's `## Build log`, which is the record. Continue from the latest entry: " +
    "after a phase, read the next phase file; after a stage inside a phase, re-read only the " +
    "section of that phase file you need (doc_section) and the dossier sections it names. " +
    "Do not redo finished phases or stages.\n\n" +
    phases
  )
}
