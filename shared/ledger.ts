// The learned-rules ledger (docs/learned-rules.md in the target repo) read by phase.
// Each `### LRN-xxxx: Title` entry may carry, on the line right after its heading,
// `<!-- phases: 2, 4 -->`: /work-on phases 0-9, `plan`, or `all`. phase_checkpoint lists
// the entries tagged for the phase the run is about to enter, as id plus title, so the
// agent opens only those (doc_section docs/learned-rules.md <id>) instead of the whole
// ledger. No ledger, no tag, or no match: nothing is listed.
import { existsSync, readFileSync } from "node:fs"
import { join } from "node:path"
import { nextLine } from "./checkpoint"

export const LEDGER_PATH = "docs/learned-rules.md"
export const MAX_LINE_CHARS = 160

export type Rule = { id: string; title: string; phases: string[]; superseded: boolean }

const HEADING_RE = /^###\s+(LRN-\d+):\s*(.+?)\s*$/
const TAG_RE = /^<!--\s*phases:\s*([^>]*?)\s*-->\s*$/

// The tagged entries of a ledger, in file order. An entry with no (or an empty) tag is
// left out. A superseded entry keeps its tag but is flagged: its successor carries the rule.
export function parseLedger(markdown: string): Rule[] {
  const lines = markdown.split("\n")
  const rules: Rule[] = []
  for (let i = 0; i < lines.length; i++) {
    const head = HEADING_RE.exec(lines[i])
    if (!head) continue
    const tag = TAG_RE.exec((lines[i + 1] ?? "").trim())
    if (!tag) continue
    const phases = tag[1].split(",").map((p) => p.trim().toLowerCase()).filter(Boolean)
    if (!phases.length) continue
    let superseded = false
    for (let j = i + 2; j < lines.length && j < i + 6 && !lines[j].startsWith("#"); j++) {
      if (/^\*\*superseded\b/i.test(lines[j].trim())) superseded = true
    }
    rules.push({ id: head[1], title: head[2], phases, superseded })
  }
  return rules
}

// What the checkpoint is about to enter: "plan" for a /plan checkpoint (named
// `Phase <n> — plan …`), else the phase number the NEXT line names (`Phase 5`, `phase-5.md`),
// else the checkpoint's own phase (a stage boundary inside a phase). undefined when none.
export function nextPhaseKey(phase: string, summary: string): string | undefined {
  if (/^\s*Phase\s+\d+\s*[—–-]+\s*plan\b/i.test(phase)) return "plan"
  const next = nextLine(summary)
  const named = next ? /\bphase[\s-]*([0-9])\b/i.exec(next) : null
  if (named) return named[1]
  const own = /^\s*Phase\s+([0-9])\b/i.exec(phase)
  return own ? own[1] : undefined
}

export function ruleLine(rule: Rule): string {
  const line = `${rule.id} ${rule.title}`
  return line.length <= MAX_LINE_CHARS ? line : line.slice(0, MAX_LINE_CHARS - 1).trimEnd() + "…"
}

// The block appended to a phase_checkpoint result, or "" when no rule matches.
export function rulesBlock(markdown: string, key: string): string {
  const hits = parseLedger(markdown).filter((r) => !r.superseded && (r.phases.includes("all") || r.phases.includes(key)))
  if (!hits.length) return ""
  const label = key === "plan" ? "plan" : `Phase ${key}`
  return (
    `\n\nLearned rules for ${label} (full text: doc_section ${LEDGER_PATH} <id>):\n` + hits.map((r) => ruleLine(r)).join("\n")
  )
}

// Reads the ledger at call time; any failure (no file, unreadable) appends nothing.
export function learnedRulesFor(dir: string, phase: string, summary: string): string {
  try {
    const key = nextPhaseKey(phase, summary)
    const file = join(dir, LEDGER_PATH)
    if (key === undefined || !existsSync(file)) return ""
    return rulesBlock(readFileSync(file, "utf8"), key)
  } catch {
    return ""
  }
}
