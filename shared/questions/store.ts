// Owner-questions store: one append-only <id>.jsonl per question (see references/owner-questions.md).
import { closeSync, existsSync, mkdirSync, openSync, readdirSync, readFileSync, statSync, writeSync } from "node:fs"
import { homedir } from "node:os"
import { join } from "node:path"

export type Line = {
  ts: string
  from: "agent" | "owner"
  kind: "question" | "reply" | "followup" | "answer"
  text: string
  via?: string
  [k: string]: unknown
}
export type Option = { key: string; label: string; consequence?: string }
export type Question = {
  id: string
  state: "open" | "answered"
  repo: string
  dossier: string
  root: string
  text: string
  options: Option[]
  recommendation: string
  blocks: string
  ts: string
  answer: Line | null
  lines: Line[]
}

export const MAX_TEXT = 3500

export function storeDir(): string {
  const dir = process.env.ASD_QUESTIONS_DIR || join(homedir(), ".agentic", "questions")
  mkdirSync(dir, { recursive: true })
  return dir
}

/** Parse the text of one question file. The id comes from the file name, never from the content. */
export function parseQuestion(id: string, content: string): Question | null {
  const lines: Line[] = []
  for (const raw of content.split("\n")) {
    if (!raw.trim()) continue
    try {
      const o = JSON.parse(raw)
      if (o && typeof o === "object" && typeof o.kind === "string") lines.push(o as Line)
    } catch {
      // a torn or foreign line is skipped
    }
  }
  const q = lines.find((l) => l.kind === "question")
  if (!q) return null
  // An option button is the owner's answer; a typed message stays a follow-up until the agent accepts it as one.
  const answer = lines.find((l) => l.kind === "answer" && (l.from === "owner" || l.accepted === true)) ?? null
  const str = (v: unknown) => (typeof v === "string" ? v : "")
  return {
    id,
    state: answer ? "answered" : "open",
    repo: str(q.repo),
    dossier: str(q.dossier),
    root: str(q.root),
    text: str(q.text),
    options: Array.isArray(q.options) ? (q.options as Option[]) : [],
    recommendation: str(q.recommendation),
    blocks: str(q.blocks),
    ts: str(q.ts),
    answer,
    lines,
  }
}

export function readQuestion(dir: string, id: string): Question | null {
  if (!/^[\w.-]+$/.test(id)) return null
  try {
    return parseQuestion(id, readFileSync(join(dir, `${id}.jsonl`), "utf8"))
  } catch {
    return null
  }
}

export function readAll(dir: string): Question[] {
  const out: Question[] = []
  for (const f of readdirSync(dir)) {
    if (!f.endsWith(".jsonl")) continue
    const q = readQuestion(dir, f.slice(0, -".jsonl".length))
    if (q) out.push(q)
  }
  return out.sort((a, b) => a.ts.localeCompare(b.ts) || a.id.localeCompare(b.id))
}

export function cut(text: string): string {
  return text.length > MAX_TEXT ? text.slice(0, MAX_TEXT) + " [cut]" : text
}

export class AnsweredError extends Error {}

/**
 * Append one owner line with a single O_APPEND write. The first answer is final:
 * a later answer (or follow-up) on an answered question throws AnsweredError.
 */
export function appendOwner(
  dir: string,
  id: string,
  m: { kind: "followup" | "answer"; text: string; option?: string; via: "page" | "slack" | "cli" },
): Question {
  const q = readQuestion(dir, id)
  if (!q) throw new Error(`no such question: ${id}`)
  if (q.state === "answered") throw new AnsweredError(`${id} is already answered`)
  const line: Line = { ts: new Date().toISOString(), from: "owner", kind: m.kind, text: cut(m.text), via: m.via }
  if (m.kind === "answer") line.option = m.option ?? ""
  const fd = openSync(join(dir, `${id}.jsonl`), "a")
  try {
    writeSync(fd, JSON.stringify(line) + "\n")
  } finally {
    closeSync(fd)
  }
  return readQuestion(dir, id)!
}

export type Sidecar = { channel: string; ts: string }

export function readSidecar(dir: string, id: string): Sidecar | null {
  try {
    const o = JSON.parse(readFileSync(join(dir, `${id}.slack.json`), "utf8"))
    return o && typeof o.channel === "string" && typeof o.ts === "string" ? o : null
  } catch {
    return null
  }
}

export function writeSidecar(dir: string, id: string, s: Sidecar): void {
  const fd = openSync(join(dir, `${id}.slack.json`), "w")
  try {
    writeSync(fd, JSON.stringify(s))
  } finally {
    closeSync(fd)
  }
}

/** Find the question whose Slack message ts (the thread root) is `threadTs`. */
export function idForThread(dir: string, channel: string, threadTs: string): string | null {
  if (!existsSync(dir) || !statSync(dir).isDirectory()) return null
  for (const f of readdirSync(dir)) {
    if (!f.endsWith(".slack.json")) continue
    const id = f.slice(0, -".slack.json".length)
    const s = readSidecar(dir, id)
    if (s && s.ts === threadTs && s.channel === channel) return id
  }
  return null
}
