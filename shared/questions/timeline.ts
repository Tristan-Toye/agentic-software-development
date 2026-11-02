// Pure builders for the run and stub timelines (newest first). Contract: references/owner-questions.md (Dashboard).
import type { Question } from "./store.ts"
import type { Update } from "./runstore.ts"

export type TLEntry = {
  ts: string
  type: "update" | "stage" | "runner" | "question" | "message" | "answer" | "log" | "entry" | "boundary"
  text: string
  auto?: boolean
  stage?: string
  label?: string
  qid?: string
  from?: string
  kind?: string
  option?: string
  via?: string
  question?: Question
}

/** The phase number of a `Phase <n>` / `PHASE <n>` mention (the letter of `4a` dropped), or "". */
export function phaseOfText(s: string): string {
  return s.match(/\b(?:Phase|PHASE)\s+(\d+)[a-z]?\b/)?.[1] ?? ""
}
const when = (ts: string): number => {
  const t = Date.parse(ts)
  return Number.isNaN(t) ? -Infinity : t
}

/** Entries of the questions asked in a run: the question, then each thread message and the answer at their own timestamps. */
export function questionEntries(questions: Question[]): TLEntry[] {
  const out: TLEntry[] = []
  for (const q of questions) {
    out.push({ ts: q.ts, type: "question", text: q.text, qid: q.id, question: q })
    let skipped = false
    for (const l of q.lines) {
      if (!skipped && l.kind === "question") { skipped = true; continue }
      out.push({ ts: l.ts, type: l.from === "owner" && l.kind === "answer" ? "answer" : "message", text: l.text, qid: q.id, from: l.from, kind: l.kind, option: typeof l.option === "string" ? l.option : undefined, via: l.via })
    }
  }
  return out
}

/**
 * One timeline, newest first: run updates, questions with their threads, `extra` entries (a stub record),
 * and build-log lines only when there are no updates. A labelled boundary is inserted whenever the phase
 * named by a `stage` line changes.
 */
export function mergeTimeline(o: { updates: Update[]; questions: Question[]; log?: string[]; extra?: TLEntry[]; names?: Record<string, string> }): TLEntry[] {
  const all: TLEntry[] = [...(o.extra ?? [])]
  if (!o.updates.length) for (const l of o.log ?? []) all.push({ ts: "", type: "log", text: l })
  // `updates` arrive newest first from the store; chronological order is restored by the stable sort below.
  for (const u of [...o.updates].reverse()) all.push({ ts: u.ts, type: u.kind, text: u.text, stage: u.stage, auto: u.auto === true ? true : undefined })
  all.push(...questionEntries(o.questions))
  const sorted = all.map((e, i) => ({ e, i, t: when(e.ts) })).sort((a, b) => (a.t === b.t ? a.i - b.i : a.t < b.t ? -1 : 1)).map((x) => x.e)
  const out: TLEntry[] = []
  let cur = ""
  for (const e of sorted) {
    if (e.type === "stage") {
      const p = phaseOfText(`${e.stage ?? ""} ${e.text}`)
      if (p && p !== cur) {
        cur = p
        out.push({ ts: e.ts, type: "boundary", text: "", label: o.names?.[p] ? `Phase ${p} — ${o.names[p]}` : `Phase ${p}` })
      }
    }
    out.push(e)
  }
  return out.reverse()
}

/** Entries of a stub record file: each `##`/`###` heading is one; `## Step N` headings are boundaries. Dated by `(YYYY-MM-DD)` in the heading, else by position (the date before it, or `cut`). */
export function parseRecord(text: string, cut = ""): TLEntry[] {
  const out: TLEntry[] = []
  const lines = text.split("\n")
  let date = cut
  let i = 0
  while (i < lines.length) {
    const m = lines[i].match(/^#{2,3}\s+(.*?)\s*$/)
    if (!m) { i++; continue }
    const body: string[] = []
    for (i++; i < lines.length && !/^#{2,3}\s/.test(lines[i]); i++) body.push(lines[i])
    const d = m[1].match(/\((\d{4}-\d{2}-\d{2})\)/)?.[1]
    if (d) date = d
    const t = body.join("\n").trim()
    out.push({ ts: date, type: /^Step\s+\d+/i.test(m[1]) ? "boundary" : "entry", label: m[1], text: t.length > 1500 ? t.slice(0, 1499) + "…" : t })
  }
  return out
}

/** The build-log bullets of a dossier, oldest first, wrapped bullets joined; for runs without updates. */
export function logEntries(log: string): string[] {
  const out: string[] = []
  let fence = false
  for (const raw of log.split("\n").slice(1)) {
    const l = raw.trim()
    if (l.startsWith("```")) { fence = !fence; continue }
    if (!l || fence) continue
    if (/^(- |\* |#{2,4} )/.test(l) || !out.length) out.push(l.replace(/^(- |\* |#{2,4} )/, ""))
    else out[out.length - 1] += " " + l
  }
  return out
}
