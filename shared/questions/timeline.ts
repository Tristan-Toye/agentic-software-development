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
  phase?: string
  option?: string
  via?: string
  question?: Question
  /** The automatic line's type (`log`, `suite`, `gates`, `pr`, `workflow`, `spawn`, `spawn-done`, `other`). */
  event?: string
  /** A spawn row's status: `running`, `done`, `gap`, `contract-change`; absent when unknown. */
  status?: string
  agentId?: string
  durationMs?: number
}

/** `all` is every entry unfiltered (spawns merged); the dashboard serves `timeline` and `activity`. */
export type View = "timeline" | "activity" | "all"

/** The event of an automatic line: its `event` field, else (older files) the text prefix. */
export function eventOf(u: { text: string; event?: string }): string {
  if (u.event) return u.event
  const t = u.text
  if (/^log:/i.test(t)) return "log"
  if (/^suite:/i.test(t)) return "suite"
  if (/^gates:/i.test(t)) return "gates"
  if (/^PR /.test(t)) return "pr"
  if (/^workflow/i.test(t)) return "workflow"
  if (/^spawned /i.test(t)) return "spawn"
  return "other"
}
const FAILURE = /\b(failed|failing|red)\b/i
/**
 * Whether an entry belongs in the Timeline: what a person wants to read, grouped by
 * stage. The agent's own notes, its questions with their threads, one line per agent
 * spawn with its status, a pull request opened or merged, and a run that failed,
 * stopped or finished. Not: command output (log copies, gates, suites, workflows),
 * checkpoint text, or the runner's bookkeeping (stage start/end, "waiting on …" —
 * the question itself is shown). The build log holds everything else.
 */
export function inTimeline(e: TLEntry): boolean {
  if (e.type === "stage") return false
  if (e.type === "runner") return /^(stage \d+ failed|failed|stopped|done|paused|resumed|switched to account)\b/i.test(e.text.trim())
  if (e.type === "update" && e.auto) {
    const ev = e.event ?? "other"
    return ev === "spawn" || ev === "pr" || ev === "blocked"
  }
  return true
}

/** Merge each `spawn` line with the later `spawn-done` of the same `agent_id` into one entry (status of the done line, duration when both timestamps parse). Chronological in, chronological out; the done lines are consumed. */
export function mergeSpawns(updates: Update[]): (Update & { doneTs?: string })[] {
  const done = new Map<string, Update>()
  for (const u of updates) if (u.auto && u.event === "spawn-done" && u.agent_id && !done.has(u.agent_id)) done.set(u.agent_id, u)
  const used = new Set<Update>()
  const out: (Update & { doneTs?: string })[] = []
  for (const u of updates) {
    if (u.auto && eventOf(u) === "spawn" && u.agent_id) {
      const d = done.get(u.agent_id)
      if (d && when(d.ts) >= when(u.ts)) { used.add(d); out.push({ ...u, status: d.status ?? u.status, doneTs: d.ts }); continue }
    }
    out.push(u)
  }
  return out.filter((u) => !used.has(u))
}

/** The timeline section a later run on a finished dossier opens. */
export const COMMAND_LABEL: Record<string, string> = { "fix-deferred": "Fix deferred issues", "product-summary": "Product summary" }
/** The phase number of a `Phase <n>` / `PHASE <n>` mention (the letter of `4a` dropped), or "". `unit` "Step" reads `Step <n>` (a stub). */
export function phaseOfText(s: string, unit: "Phase" | "Step" = "Phase"): string {
  return (unit === "Step" ? s.match(/\bStep\s+(\d+)\b/) : s.match(/\b(?:Phase|PHASE)\s+(\d+)[a-z]?\b/))?.[1] ?? ""
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
 * One timeline, newest first: run updates and questions with their threads, plus `extra` entries (the step
 * boundaries of a stub). It never falls back to the build log or the record: with no updates and no questions it
 * is empty. A labelled boundary is inserted whenever the phase (or `Step`) named by a `stage` line changes.
 */
export function mergeTimeline(o: { updates: Update[]; questions: Question[]; extra?: TLEntry[]; names?: Record<string, string>; unit?: "Phase" | "Step"; view?: View }): TLEntry[] {
  const unit = o.unit ?? "Phase"
  const all: TLEntry[] = [...(o.extra ?? [])]
  // `updates` arrive newest first from the store; chronological order is restored by the stable sort below.
  const view = o.view ?? "timeline"
  const chrono = [...o.updates].reverse()
  for (const u of view !== "activity" ? mergeSpawns(chrono) : chrono) {
    const e: TLEntry = { ts: u.ts, type: u.kind, text: u.text, stage: u.stage, auto: u.auto === true ? true : undefined }
    if (u.auto) {
      e.event = eventOf(u)
      if (u.status) e.status = u.status
      if (e.event === "spawn") {
        if (u.agent_id) e.agentId = u.agent_id
        const dt = (u as { doneTs?: string }).doneTs
        if (dt && when(dt) > -Infinity && when(u.ts) > -Infinity) e.durationMs = Math.max(0, when(dt) - when(u.ts))
        if (e.text.startsWith("spawned ")) e.text = e.text.slice(8)
      }
    }
    all.push(e)
  }
  if (view !== "activity") all.push(...questionEntries(o.questions))
  const sorted = all.map((e, i) => ({ e, i, t: when(e.ts) })).sort((a, b) => (a.t === b.t ? a.i - b.i : a.t < b.t ? -1 : 1)).map((x) => x.e)
  const out: TLEntry[] = []
  let cur = ""
  for (const e of sorted) {
    if (e.type === "boundary" && e.phase) cur = e.phase
    // A later run on the same dossier (/fix-deferred …) is a section of its own; its steps stay inside it even when
    // they name a /work-on phase they reuse. A /work-on run after one starts counting phases again; a resumed one carries on.
    const cmd = e.type === "runner" ? e.text.match(/^start \/(?:[\w-]+:)?([\w-]+)\b/)?.[1] : undefined
    if (cmd && cmd !== "work-on" && cmd !== "stub") {
      cur = `cmd:${cmd}`
      out.push({ ts: e.ts, type: "boundary", text: "", phase: cur, label: COMMAND_LABEL[cmd] ?? `/${cmd}` })
    } else if (cmd && cur.startsWith("cmd:")) cur = ""
    if (e.type === "stage" && !cur.startsWith("cmd:")) {
      const p = phaseOfText(`${e.stage ?? ""} ${e.text}`, unit)
      if (p && p !== cur) {
        cur = p
        out.push({ ts: e.ts, type: "boundary", text: "", phase: p, label: unit === "Step" ? `Step ${p}` : o.names?.[p] ? `Phase ${p} \u2014 ${o.names[p]}` : `Phase ${p}` })
      }
    }
    // Boundaries come from every stage line; each view then keeps only its own entries.
    if (e.type === "boundary" || (view === "all" || (view === "timeline" ? inTimeline(e) : e.type === "update" && e.auto === true))) out.push(e)
  }
  return out.reverse()
}

// ---- classification and grouping (pure) ----
export type Kind = "blocked" | "spawn" | "start" | "stage" | "question" | "answered" | "suite-green" | "suite-red" | "gate" | "pr" | "workflow" | "failed" | "waiting" | "done" | "stopped" | "note" | "reply" | "routine" | "info" | "entry" | "boundary"
const KEY_KINDS = new Set<Kind>(["blocked", "start", "stage", "question", "answered", "suite-green", "suite-red", "gate", "pr", "workflow", "failed", "waiting", "done", "stopped"])

/** What an entry is, for display: its kind, whether it is a key event, and whether it is routine (collapsible). */
export function classify(e: TLEntry): { kind: Kind; key: boolean; routine: boolean } {
  const mk = (kind: Kind) => ({ kind, key: KEY_KINDS.has(kind), routine: kind === "routine" || kind === "info" })
  const t = e.text
  switch (e.type) {
    case "boundary": return mk("boundary")
    case "question": return mk("question")
    case "answer": return mk("answered")
    case "message": return mk("reply")
    case "entry": return mk("entry")
    case "stage": return mk("stage")
    case "runner":
      if (/^start\b/i.test(t)) return mk("start")
      if (/^waiting\b/i.test(t)) return mk("waiting")
      if (/^(answered|follow-up)\b/i.test(t)) return mk("answered")
      if (/^done\b/i.test(t)) return mk("done")
      if (/^stopped\b/i.test(t)) return mk("stopped")
      if (/\bfailed\b/i.test(t)) return mk("failed")
      if (/^stage \S+ end\b/i.test(t)) return mk("info")
      return mk("info")
    default: break
  }
  // update (hook-written `auto` lines and agent notes)
  if (e.event === "spawn") return mk("spawn")
  if (e.auto && e.event === "blocked") return mk("blocked")
  if (e.auto && e.event === "workflow") return mk("workflow")
  // An agent's note is prose: "the gate failed, then passed alone after a fixture rebuild" is no red suite.
  // Suite, gate and PR kinds come only from the hook's own lines, which carry the real result.
  if (!e.auto) return mk(/^\s*(?:\*\*)?[A-Z][A-Z0-9]*-\d+ is done\b/.test(t) ? "done" : "note")
  if (e.event === "suite") {
    // "suite: passed 31  failed 0  ignored 0": the count decides; "suite run finished" says nothing either way.
    const n = t.match(/\bfailed\s+(\d+)/i)
    if (n) return mk(Number(n[1]) > 0 ? "suite-red" : "suite-green")
    if (/\b(red|fail(?:ed|ing|s)?)\b/i.test(t)) return mk("suite-red")
    return mk(/\b(green|pass(?:ed|es|ing)?|ok)\b/i.test(t) ? "suite-green" : "routine")
  }
  if (e.event === "gates") return mk("gate")
  if (e.event === "pr") return mk("pr")
  if (/\b(tests?|suite)\b[^.]*\b(red|fail(?:ed|ing|s)?)\b|\b(red|fail(?:ed|ing)?)\b[^.]*\b(tests?|suite)\b/i.test(t)) return mk("suite-red")
  if (/\b(tests?|suite)\b[^.]*\b(green|pass(?:ed|es|ing)?|ok)\b|\b(green|passing)\b[^.]*\b(tests?|suite)\b/i.test(t)) return mk("suite-green")
  if (/\bgates?\b/i.test(t)) return mk("gate")
  if (/\b(PR|pull request)\b[^.]*\b(open(?:ed)?|merged|created)\b|\b(opened|merged|created)\b[^.]*\b(PR|pull request)\b/i.test(t)) return mk("pr")
  if (/\bworkflow\b/i.test(t)) return mk("workflow")
  if (/\bfailed\b/i.test(t)) return mk("failed")
  return mk(e.auto ? "routine" : "note")
}

export type Item = { kind: Kind; key: boolean; entry: TLEntry } | { kind: "more"; key: false; entries: { kind: Kind; entry: TLEntry }[] }
export type Group = {
  id: string; label: string; phase: string; current: boolean; start: string; end: string; durationMs: number | null
  summary: string; counts: { questions: number; updates: number }; items: Item[]
}
const firstLine = (s: string): string => s.split("\n").map((l) => l.trim()).find(Boolean) ?? ""

/**
 * Group a newest-first timeline by its boundaries; groups newest first, the first one is current. A question and
 * its thread belong to the phase of the question's first message. Inside a group items are newest first and runs
 * of two or more routine entries collapse into one `more` item.
 */
export function groupByPhase(entries: TLEntry[], first = "Setup", collapse = true): Group[] {
  type G = { g: Group; raw: { e: TLEntry; i: number }[] }
  const groups: G[] = []
  const qgroup = new Map<string, G>()
  let cur: G | null = null
  const open = (label: string, phase: string, start: string, summary: string): G => {
    const g: G = { g: { id: `${groups.length}:${label}`, label, phase, current: false, start, end: start, durationMs: null, summary, counts: { questions: 0, updates: 0 }, items: [] }, raw: [] }
    groups.push(g)
    return g
  }
  const chrono = [...entries].reverse()
  chrono.forEach((e, i) => {
    if (e.type === "boundary") { cur = open(e.label ?? "", e.phase ?? "", e.ts, firstLine(e.text)); return }
    if (!cur) cur = open(first, "", e.ts, "")
    let g = cur
    if (e.qid && e.type === "question") qgroup.set(e.qid, cur)
    else if (e.qid && (e.type === "message" || e.type === "answer")) g = qgroup.get(e.qid) ?? cur
    g.raw.push({ e, i })
    if (e.type === "question") g.g.counts.questions++
    else if (e.type === "update" || e.type === "stage" || e.type === "runner") g.g.counts.updates++
    if (e.type === "stage" && !g.g.summary) g.g.summary = firstLine(e.text)
    else if (e.type === "stage") g.g.summary = firstLine(e.text) || g.g.summary
  })
  const out: Group[] = []
  groups.forEach((G, gi) => {
    const sorted = [...G.raw].sort((a, b) => (when(a.e.ts) === when(b.e.ts) ? b.i - a.i : when(a.e.ts) < when(b.e.ts) ? 1 : -1))
    const items: Item[] = []
    let run: { kind: Kind; entry: TLEntry }[] = []
    const flush = () => {
      if (run.length === 1) items.push({ kind: run[0].kind, key: false, entry: run[0].entry })
      else if (run.length > 1) items.push({ kind: "more", key: false, entries: run })
      run = []
    }
    for (const { e } of sorted) {
      const c = classify(e)
      if (collapse && c.routine) run.push({ kind: c.kind, entry: e })
      else { flush(); items.push({ kind: c.kind, key: c.key, entry: e }) }
    }
    flush()
    G.g.items = items
    const ts = G.raw.map((r) => when(r.e.ts)).filter((t) => t > -Infinity)
    const end = ts.length ? Math.max(...ts) : NaN
    const next = groups[gi + 1]
    const nextStart = next ? when(next.g.start) : -Infinity
    const s = when(G.g.start)
    const stop = nextStart > -Infinity ? nextStart : end
    G.g.end = Number.isNaN(stop) || stop === -Infinity ? G.g.start : new Date(stop).toISOString()
    G.g.durationMs = s > -Infinity && stop > -Infinity && !Number.isNaN(stop) ? Math.max(0, stop - s) : null
    out.push(G.g)
  })
  out.reverse()
  if (out.length) out[0].current = true
  return out
}

export type NowPanel = {
  phase: string; step: number | null; since: string
  doing: { text: string; ts: string; kind: Kind; auto: boolean } | null
  runner: string; waiting: string[]
}
/** The data of the "Now" panel: the current phase, what the run is doing (newest meaningful entry), the runner state, and the open questions waiting on the owner. */
export function nowPanel(entries: TLEntry[], groups: Group[], o: { runner?: string | null; waiting?: string[]; fallbackPhase?: string }): NowPanel {
  const g = groups[0]
  const phase = g?.phase || o.fallbackPhase || ""
  const num = /^\d+$/.test(phase) ? Number(phase) : null
  let doing: NowPanel["doing"] = null
  let any: NowPanel["doing"] = null
  for (const e of entries) {
    if (e.type === "boundary" || e.type === "entry" || e.type === "log") continue
    const c = classify(e)
    const d = { text: e.text, ts: e.ts, kind: c.kind, auto: e.auto === true }
    if (!any) any = d
    if (!c.routine) { doing = d; break }
  }
  return { phase: g?.label ?? "", step: num, since: g?.start ?? "", doing: doing ?? any, runner: o.runner ?? "", waiting: o.waiting ?? [] }
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
    out.push({ ts: date, type: /^Step\s+\d+/i.test(m[1]) ? "boundary" : "entry", phase: m[1].match(/^Step\s+(\d+)/i)?.[1], label: m[1], text: t.length > 1500 ? t.slice(0, 1499) + "…" : t })
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
