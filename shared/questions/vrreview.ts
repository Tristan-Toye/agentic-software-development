// Visual review (VR-04): the owner's verdict per diagram element, kept in .discovery/visual-review/<ID>/review.json
// (shape: references/visual-review/review.schema.json), and the answer text built from them for the one product question.
// Pure file work, no server state: the routes in server.ts call these.
import { existsSync, mkdirSync, readdirSync, readFileSync, renameSync, statSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { archStemOf, archStems, DECISION_VIEW, approvalHash, graphHash, reviewViews, viewFile, VR_VIEWS } from "./vrlayout.ts"

export const VERDICTS = ["ok", "question", "change", "remove"] as const
export type Verdict = (typeof VERDICTS)[number]
/** A diagram of the review: L0, L1, L2, `decision` (decision.json, when it exists), or `arch:<stem>` for a new architecture diagram drafted in arch/<stem>.json. */
export type Diagram = (typeof VR_VIEWS)[number] | typeof DECISION_VIEW | `arch:${string}`
const isDiagramName = (d: unknown): d is Diagram => (VR_VIEWS as readonly unknown[]).includes(d) || d === DECISION_VIEW || archStemOf(d) !== null
export type Anchor = { diagram: Diagram; element: string; graph_hash: string }
export type ReviewLine = { anchor: Anchor; verdict: Verdict; comment: string; ts: string; blocking?: boolean }
/**
 * Feedback on an asset other than a diagram element (unified feedback control): the product summary (`summary`), a dossier
 * section (`doc:<slug>`), an ADR (`adr:<id>`), a deferred item (`deferred:<id>`) or a pane of the page (`pane:<key>`). `viewed`
 * lists the anchors the owner marked as looked at, element anchors (`el:<diagram>:<element>`) included. Both are optional in
 * review.json: a file written before them still loads, and a file without them is written back without them.
 */
export const NOTE_VERDICTS = ["ok", "question", "change", "comment"] as const
export type NoteVerdict = (typeof NOTE_VERDICTS)[number]
export type Note = { anchor: string; verdict: NoteVerdict; comment: string; blocking: boolean; ts: string }
export type Review = { schema_version: "1.0.0"; dossier: string; updated: string; lines: ReviewLine[]; notes?: Note[]; viewed?: string[] }
export const MAX_NOTES = 300
export const MAX_VIEWED = 800
/** summary, or one of the asset prefixes with an id of letters, digits and . _ : - (an element id may hold a colon). */
export const isAssetAnchor = (a: unknown): a is string => typeof a === "string" && a.length <= MAX_ID && /^(summary|(doc|adr|deferred|pane|el):[A-Za-z0-9][A-Za-z0-9_.:\-]*)$/.test(a)
/** current: made on the graph as it is now. outdated: made on an older graph. gone: the element is no longer in the diagram. */
export type LineState = "current" | "outdated" | "gone"
export type StateLine = ReviewLine & { state: LineState }
export type Summary = { total: number; checked: number; ok: number; question: number; change: number; remove: number; outdated: number; gone: number; unchecked: number; text: string }
export type ReviewState = { exists: boolean; notes: Note[]; viewed: string[]; broken: Diagram[]; hashes: Partial<Record<Diagram, string>>; lines: StateLine[]; summary: Summary; unchecked: { diagram: Diagram; element: string }[] }

export const MAX_COMMENT = 1000
export const MAX_ID = 200
export const MAX_BODY = 8 * 1024
export const MAX_FEEDBACK_BODY = 64 * 1024
export const MAX_LINES = 600
export const MAX_FILE = 512 * 1024
/** A diagram file larger than this is not parsed: it counts as unreadable. */
export const MAX_GRAPH_FILE = 4 * 1024 * 1024
/** The store cuts a line at 3500 characters; an answer stays well under it. */
export const MAX_ANSWER = 3300

export class ReviewError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

export const reviewFile = (dir: string): string => join(dir, "review.json")

type Graphs = Partial<Record<string, { hash: string; legacy: string; nodes: Set<string>; labels: Map<string, string> }>>
/**
 * The nodes (the elements a verdict can be about) and the hash of each diagram file that exists and parses. `broken`
 * names each file that exists but is too large, does not parse or has no nodes array (e.g. caught mid-write): the review
 * cannot be Approved while one is broken.
 */
export function readGraphsFull(dir: string): { graphs: Graphs; broken: Diagram[] } {
  const graphs: Graphs = {}
  const broken: Diagram[] = []
  for (const v of reviewViews(dir) as Diagram[]) {
    const file = viewFile(dir, v)!
    try {
      if (!existsSync(file)) continue
      const st = statSync(file)
      if (!st.isFile() || st.size > MAX_GRAPH_FILE) { broken.push(v); continue }
      const g = JSON.parse(readFileSync(file, "utf8"))
      if (!g || !Array.isArray(g.nodes)) { broken.push(v); continue }
      const labels = new Map<string, string>()
      for (const n of g.nodes) if (n && typeof n.id === "string") labels.set(n.id, typeof n.label === "string" ? n.label : n.id)
      graphs[v] = { hash: approvalHash(g), legacy: graphHash(g), nodes: new Set(labels.keys()), labels }
    } catch { broken.push(v) }
  }
  return { graphs, broken }
}
export const readGraphs = (dir: string): Graphs => readGraphsFull(dir).graphs

/** The lines of review.json that have the right shape; anything else in the file is ignored (and rewritten away on the next save). */
export function readReview(dir: string, dossier: string): Review | null {
  return readReviewChecked(dir, dossier).review
}
/** Like readReview, but says why there is none: `unreadable` is set when review.json exists and cannot be used (oversize, not JSON, no lines array); a missing file is just `review: null`. */
export function readReviewChecked(dir: string, dossier: string): { review: Review | null; unreadable?: string } {
  const file = reviewFile(dir)
  try {
    if (!existsSync(file)) return { review: null }
    if (statSync(file).size > MAX_FILE) return { review: null, unreadable: "review.json is larger than " + MAX_FILE + " bytes" }
    const o = JSON.parse(readFileSync(file, "utf8"))
    if (!o || typeof o !== "object" || !Array.isArray(o.lines)) return { review: null, unreadable: "review.json has no lines list" }
    const lines: ReviewLine[] = []
    for (const l of o.lines) {
      const a = l?.anchor
      if (a && isDiagramName(a.diagram) && typeof a.element === "string" && typeof a.graph_hash === "string" && (VERDICTS as readonly string[]).includes(l.verdict))
        lines.push({ anchor: { diagram: a.diagram, element: a.element, graph_hash: a.graph_hash }, verdict: l.verdict, comment: typeof l.comment === "string" ? l.comment : "", ts: typeof l.ts === "string" ? l.ts : "", ...(l.blocking === true ? { blocking: true } : {}) })
    }
    const notes: Note[] = []
    for (const n of Array.isArray(o.notes) ? o.notes : []) {
      if (n && isAssetAnchor(n.anchor) && (NOTE_VERDICTS as readonly string[]).includes(n.verdict))
        notes.push({ anchor: n.anchor, verdict: n.verdict, comment: typeof n.comment === "string" ? n.comment : "", blocking: n.blocking === true, ts: typeof n.ts === "string" ? n.ts : "" })
    }
    const viewed = [...new Set((Array.isArray(o.viewed) ? o.viewed : []).filter(isAssetAnchor))] as string[]
    return { review: { schema_version: "1.0.0", dossier: typeof o.dossier === "string" ? o.dossier : dossier, updated: typeof o.updated === "string" ? o.updated : "", lines, ...(notes.length ? { notes } : {}), ...(viewed.length ? { viewed } : {}) } }
  } catch (e) { return { review: null, unreadable: "review.json cannot be read: " + (e as Error).message } }
}

/** The verdicts against the diagrams as they are now, and the count line ("12 of 14 elements checked"). Outdated and gone lines are kept, not counted. */
export function reviewState(dir: string, dossier: string): ReviewState {
  const { graphs, broken } = readGraphsFull(dir)
  const review = readReview(dir, dossier)
  const lines: StateLine[] = (review?.lines ?? []).map((l) => {
    const g = graphs[l.anchor.diagram]
    const state: LineState = !g || !g.nodes.has(l.anchor.element) ? "gone" : g.hash !== l.anchor.graph_hash && g.legacy !== l.anchor.graph_hash ? "outdated" : "current"
    return { ...l, state }
  })
  const cur = new Map(lines.filter((l) => l.state === "current").map((l) => [l.anchor.diagram + "\u0000" + l.anchor.element, l]))
  const unchecked: { diagram: Diagram; element: string }[] = []
  let total = 0
  for (const v of Object.keys(graphs) as Diagram[]) for (const id of graphs[v]?.nodes ?? []) { total++; if (!cur.has(v + "\u0000" + id)) unchecked.push({ diagram: v, element: id }) }
  const n = (k: Verdict) => [...cur.values()].filter((l) => l.verdict === k).length
  const s = { total, checked: cur.size, ok: n("ok"), question: n("question"), change: n("change"), remove: n("remove"), outdated: lines.filter((l) => l.state === "outdated").length, gone: lines.filter((l) => l.state === "gone").length, unchecked: unchecked.length }
  const bits = [s.question && s.question + " question" + (s.question > 1 ? "s" : ""), s.change && s.change + " change" + (s.change > 1 ? "s" : ""), s.remove && s.remove + " to remove", s.outdated && s.outdated + " outdated"].filter(Boolean)
  if (broken.length) bits.push("unreadable: " + broken.join(", "))
  const text = s.checked + " of " + s.total + " elements checked" + (bits.length ? ", " + bits.join(", ") : "")
  return { exists: review !== null, notes: review?.notes ?? [], viewed: review?.viewed ?? [], broken, hashes: Object.fromEntries(Object.keys(graphs).map((v) => [v, graphs[v]!.hash])), lines, summary: { ...s, text }, unchecked }
}

/** `graph_hash` is the hash of the graph the owner saw (from the review state's `hashes`); when given it must match the file on disk. */
export type VerdictInput = { diagram: unknown; element: unknown; verdict: unknown; comment?: unknown; graph_hash?: unknown; blocking?: unknown }
/**
 * Upsert (or clear, with verdict "unset") one verdict. Refuses an unknown diagram, an element that is not a node of the
 * current graph, an unknown verdict and an oversize comment. `followup` is set when the line is a Question with a comment:
 * the route sends it to the agent as a follow-up that carries the same anchor.
 */
export function setVerdict(dir: string, dossier: string, b: VerdictInput): { state: ReviewState; followup: { anchor: Anchor; text: string } | null } {
  if (!isDiagramName(b.diagram)) throw new ReviewError(400, "diagram must be L0, L1, L2, decision or arch:<stem>")
  const diagram = b.diagram as Diagram
  if (typeof b.element !== "string" || !b.element || b.element.length > MAX_ID) throw new ReviewError(400, "element must be an id of at most " + MAX_ID + " characters")
  const unset = b.verdict === "unset"
  if (!unset && !(VERDICTS as readonly string[]).includes(b.verdict as string)) throw new ReviewError(400, "verdict must be one of " + VERDICTS.join(", ") + " or unset")
  const comment = b.comment === undefined || b.comment === null ? "" : b.comment
  if (typeof comment !== "string") throw new ReviewError(400, "comment must be text")
  if (comment.length > MAX_COMMENT) throw new ReviewError(413, "comment is longer than " + MAX_COMMENT + " characters")
  const g = readGraphs(dir)[diagram]
  // Clearing ("unset") also works for a line whose element or diagram is gone, so stale lines can be removed.
  if (!unset && !g) throw new ReviewError(404, "no " + diagram + " diagram for " + dossier)
  if (!unset && !g!.nodes.has(b.element)) throw new ReviewError(400, "unknown element " + b.element + " in " + diagram)
  if (!unset && b.graph_hash !== undefined && b.graph_hash !== null && (typeof b.graph_hash !== "string" || (g && b.graph_hash !== g.hash)))
    throw new ReviewError(409, "the diagram changed since you opened it; reload")
  const cur = readReviewChecked(dir, dossier)
  if (cur.unreadable) throw new ReviewError(409, cur.unreadable + "; verdicts were not saved so the file is not overwritten. Fix or remove " + reviewFile(dir) + " and reload")
  const prev = cur.review ?? { schema_version: "1.0.0" as const, dossier, updated: "", lines: [] }
  const same = (l: ReviewLine) => l.anchor.diagram === diagram && l.anchor.element === b.element
  const old = prev.lines.find(same)
  const lines = prev.lines.filter((l) => !same(l))
  const ts = new Date().toISOString()
  let followup: { anchor: Anchor; text: string } | null = null
  if (!unset) {
    const anchor: Anchor = { diagram, element: b.element, graph_hash: g!.hash }
    lines.push({ anchor, verdict: b.verdict as Verdict, comment: comment.trim(), ts, ...(b.blocking === true ? { blocking: true } : {}) })
    if (b.verdict === "question" && comment.trim() && !(old && old.verdict === "question" && old.comment === comment.trim() && old.anchor.graph_hash === g!.hash)) followup = { anchor, text: comment.trim() }
  }
  if (lines.length > MAX_LINES) throw new ReviewError(413, "review.json would hold more than " + MAX_LINES + " verdicts")
  const body = reviewBody(dossier, ts, lines, prev)
  if (body.length > MAX_FILE) throw new ReviewError(413, "review.json would be larger than " + MAX_FILE + " bytes")
  mkdirSync(dir, { recursive: true })
  const tmp = reviewFile(dir) + "." + process.pid + ".tmp"
  writeFileSync(tmp, body)
  renameSync(tmp, reviewFile(dir))
  return { state: reviewState(dir, dossier), followup }
}

/** The body of the one "Send review" submit: asset notes (verdict "clear" removes one), viewed marks added and removed. */
export type FeedbackInput = { notes?: unknown; viewed_add?: unknown; viewed_remove?: unknown }
/**
 * Apply a whole local draft in one write: upsert (or clear) the notes by anchor, add and remove viewed anchors. Nothing is
 * written when any entry is invalid. `followups` are the notes that need the agent (a Question, a Change, a blocking note, or a
 * comment with text) and that are new or changed, for the route to send as follow-ups. Diagram-element verdicts are not part of
 * this: they keep their own route (setVerdict).
 */
export function submitFeedback(dir: string, dossier: string, b: FeedbackInput): { state: ReviewState; followups: Note[] } {
  const rawNotes = b.notes === undefined ? [] : b.notes, add = b.viewed_add === undefined ? [] : b.viewed_add, rem = b.viewed_remove === undefined ? [] : b.viewed_remove
  if (!Array.isArray(rawNotes) || !Array.isArray(add) || !Array.isArray(rem)) throw new ReviewError(400, "notes, viewed_add and viewed_remove must be lists")
  if (rawNotes.length > MAX_NOTES || add.length > MAX_VIEWED || rem.length > MAX_VIEWED) throw new ReviewError(413, "too many entries in one review")
  for (const a of [...add, ...rem]) if (!isAssetAnchor(a)) throw new ReviewError(400, "bad anchor in viewed: " + String(a).slice(0, 60))
  const incoming: { anchor: string; verdict: NoteVerdict | "clear"; comment: string; blocking: boolean }[] = []
  for (const n of rawNotes as any[]) {
    if (!n || typeof n !== "object" || !isAssetAnchor(n.anchor)) throw new ReviewError(400, "a note needs an anchor: summary, doc:<slug>, adr:<id>, deferred:<id> or pane:<key>")
    if (n.verdict !== "clear" && !(NOTE_VERDICTS as readonly string[]).includes(n.verdict)) throw new ReviewError(400, "note verdict must be one of " + NOTE_VERDICTS.join(", ") + " or clear")
    const comment = n.comment === undefined || n.comment === null ? "" : n.comment
    if (typeof comment !== "string") throw new ReviewError(400, "comment must be text")
    if (comment.length > MAX_COMMENT) throw new ReviewError(413, "comment is longer than " + MAX_COMMENT + " characters")
    incoming.push({ anchor: n.anchor, verdict: n.verdict, comment: comment.trim(), blocking: n.blocking === true })
  }
  const cur = readReviewChecked(dir, dossier)
  if (cur.unreadable) throw new ReviewError(409, cur.unreadable + "; the review was not saved so the file is not overwritten. Fix or remove " + reviewFile(dir) + " and reload")
  const prev = cur.review ?? { schema_version: "1.0.0" as const, dossier, updated: "", lines: [] }
  const ts = new Date().toISOString()
  const notes = new Map((prev.notes ?? []).map((n) => [n.anchor, n]))
  const followups: Note[] = []
  for (const n of incoming) {
    const old = notes.get(n.anchor)
    if (n.verdict === "clear") { notes.delete(n.anchor); continue }
    const note: Note = { anchor: n.anchor, verdict: n.verdict, comment: n.comment, blocking: n.blocking, ts }
    notes.set(n.anchor, note)
    const same = old && old.verdict === note.verdict && old.comment === note.comment && old.blocking === note.blocking
    if (!same && (note.verdict === "question" || note.verdict === "change" || note.blocking || (note.verdict === "comment" && note.comment))) followups.push(note)
  }
  const viewed = new Set(prev.viewed ?? [])
  for (const a of add as string[]) viewed.add(a)
  for (const a of rem as string[]) viewed.delete(a)
  if (notes.size > MAX_NOTES || viewed.size > MAX_VIEWED) throw new ReviewError(413, "review.json would hold too many notes or viewed marks")
  const body = reviewBody(dossier, ts, prev.lines, prev, { notes: [...notes.values()], viewed: [...viewed] })
  if (body.length > MAX_FILE) throw new ReviewError(413, "review.json would be larger than " + MAX_FILE + " bytes")
  mkdirSync(dir, { recursive: true })
  const tmp = reviewFile(dir) + "." + process.pid + ".tmp"
  writeFileSync(tmp, body)
  renameSync(tmp, reviewFile(dir))
  return { state: reviewState(dir, dossier), followups }
}

/**
 * The one-click approval: every element of every diagram (L0, L1, L2, the decision flow when it exists and each architecture tab) gets an OK, in one write.
 * An OK already made on the current graph keeps its comment; an outdated verdict is replaced (the owner approves the picture
 * as it is now); a line whose element is gone is kept. Refused while an element carries a current Question, Change or
 * Remove (that review says changes requested: clear those first), while a diagram file is unreadable, or when `shown`
 * (the hashes of the graphs the owner saw) no longer matches a file on disk.
 */
export function approveAll(dir: string, dossier: string, shown?: unknown): ReviewState {
  const { graphs, broken } = readGraphsFull(dir)
  if (broken.length) throw new ReviewError(409, "unreadable diagram file: " + broken.join(", ") + "; it cannot be approved")
  const views = Object.keys(graphs) as Diagram[]
  if (!views.length) throw new ReviewError(404, "no diagrams for " + dossier)
  if (shown !== undefined && shown !== null) {
    if (typeof shown !== "object" || Array.isArray(shown)) throw new ReviewError(400, "hashes must be {diagram: hash}")
    for (const [v, h] of Object.entries(shown as Record<string, unknown>)) if (graphs[v] && h !== graphs[v]!.hash) throw new ReviewError(409, "the diagram changed since you opened it; reload")
  }
  const cur = readReviewChecked(dir, dossier)
  if (cur.unreadable) throw new ReviewError(409, cur.unreadable + "; verdicts were not saved so the file is not overwritten. Fix or remove " + reviewFile(dir) + " and reload")
  const before = reviewState(dir, dossier)
  const flagged = before.lines.filter((l) => l.state === "current" && l.verdict !== "ok")
  const blk = blockingNotes(before.notes)
  if (blk.length) throw new ReviewError(409, blk.length + " blocking note" + (blk.length > 1 ? "s" : "") + " (" + blk.slice(0, 5).map((n) => n.anchor).join(", ") + ") must be cleared or made informing first")
  if (flagged.length) throw new ReviewError(409, flagged.length + " element" + (flagged.length > 1 ? "s are" : " is") + " marked " + [...new Set(flagged.map((l) => l.verdict))].join("/") + " (" + flagged.slice(0, 5).map((l) => l.anchor.diagram + " " + l.anchor.element).join(", ") + (flagged.length > 5 ? ", …" : "") + "): clear those verdicts first, or send the review as it is")
  const ts = new Date().toISOString()
  const okNow = new Map(before.lines.filter((l) => l.state === "current").map((l) => [l.anchor.diagram + "\u0000" + l.anchor.element, l]))
  const lines: ReviewLine[] = (cur.review?.lines ?? []).filter((l) => { const g = graphs[l.anchor.diagram]; return !g || !g.nodes.has(l.anchor.element) })
  for (const v of views) for (const id of graphs[v]!.nodes) {
    const keep = okNow.get(v + "\u0000" + id)
    lines.push(keep ? { anchor: keep.anchor, verdict: "ok", comment: keep.comment, ts: keep.ts } : { anchor: { diagram: v, element: id, graph_hash: graphs[v]!.hash }, verdict: "ok", comment: "", ts })
  }
  if (lines.length > MAX_LINES) throw new ReviewError(413, "review.json would hold more than " + MAX_LINES + " verdicts")
  const body = reviewBody(dossier, ts, lines, cur.review)
  if (body.length > MAX_FILE) throw new ReviewError(413, "review.json would be larger than " + MAX_FILE + " bytes")
  mkdirSync(dir, { recursive: true })
  const tmp = reviewFile(dir) + "." + process.pid + ".tmp"
  writeFileSync(tmp, body)
  renameSync(tmp, reviewFile(dir))
  return reviewState(dir, dossier)
}

/** The text of review.json; notes and viewed are written only when there are some, so a file without them keeps its old shape. */
function reviewBody(dossier: string, ts: string, lines: ReviewLine[], prev: Review | null | undefined, over?: { notes?: Note[]; viewed?: string[] }): string {
  const notes = over?.notes ?? prev?.notes ?? [], viewed = over?.viewed ?? prev?.viewed ?? []
  return JSON.stringify({ schema_version: "1.0.0", dossier, updated: ts, lines, ...(notes.length ? { notes } : {}), ...(viewed.length ? { viewed } : {}) }, null, 1) + "\n"
}
/** Notes that hold the approval back: a Question or a Change the owner flagged as blocking (an informing one does not). */
const blockingNotes = (notes: Note[]): Note[] => notes.filter((n) => n.blocking && (n.verdict === "question" || n.verdict === "change"))
const clip = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s)
/**
 * The answer for the visual-review question, as the Phase 3 step defines it. Option A (Approved) only when every element
 * has a current OK. Otherwise option B (Changes requested), with one line per flagged element: verdict, diagram, element
 * id, comment. Elements not yet looked at and verdicts made on an older graph are named; review.json holds the full text.
 */
export function buildAnswer(state: ReviewState, dossier: string): { option: "A" | "B"; text: string } {
  const s = state.summary
  const head = (dossier ? dossier + " " : "") + "visual review: "
  if (s.total > 0 && s.ok === s.total && !state.broken.length && !blockingNotes(state.notes).length) return { option: "A", text: head + "approved, all " + s.total + " elements OK." }
  const label: Record<Verdict, string> = { ok: "ok", question: "question", change: "change", remove: "remove from scope" }
  const flagged = state.lines.filter((l) => l.state === "current" && l.verdict !== "ok")
  const out = [head + "changes requested. " + s.text + ". Full verdicts: .discovery/visual-review/" + dossier + "/review.json"]
  for (const l of flagged) out.push("- " + (l.blocking ? "BLOCKING " : "") + label[l.verdict] + " " + l.anchor.diagram + " " + l.anchor.element + (l.comment ? ": " + clip(l.comment.replace(/\s+/g, " "), 240) : ""))
  for (const n of state.notes) if (n.verdict !== "ok" && (n.blocking || n.verdict !== "comment")) out.push("- " + (n.blocking ? "BLOCKING " : "") + n.verdict + " on " + n.anchor + (n.comment ? ": " + clip(n.comment.replace(/\s+/g, " "), 240) : ""))
  const old = state.lines.filter((l) => l.state === "outdated")
  if (old.length) out.push("- outdated (made on an older graph): " + old.map((l) => l.anchor.diagram + " " + l.anchor.element).join(", "))
  if (state.broken.length) out.push("- unreadable diagram file (not reviewed): " + state.broken.join(", "))
  if (state.unchecked.length) out.push("- not yet looked at: " + state.unchecked.map((u) => u.diagram + " " + u.element).join(", "))
  let text = out.join("\n")
  if (text.length > MAX_ANSWER) {
    const keep: string[] = []
    let n = 0
    for (const l of out) { if (n + l.length + 1 > MAX_ANSWER - 80) break; keep.push(l); n += l.length + 1 }
    text = keep.join("\n") + "\n- (cut: the rest is in review.json)"
  }
  return { option: "B", text }
}

/** The `topic` marker `ask_owner.py ask --topic visual-review` writes on the visual-review question. */
export const VR_TOPIC = "visual-review"

/** True for the dossier's visual-review question: the explicit marker, a product question, and (a secondary guard) the Approved / Changes requested labels. */
export function isReviewQuestion(q: { topic?: string; qkind: string; options: { key: string; label: string }[] }): boolean {
  return q.topic === VR_TOPIC && q.qkind === "product" && !!reviewOptions(q.options)
}

/** The option keys of the question for "Approved" and "Changes requested"; null when the question is not the visual-review one. */
export function reviewOptions(options: { key: string; label: string }[]): { A: string; B: string } | null {
  const a = options.find((o) => /^approved\b/i.test(o.label)), b = options.find((o) => /^changes requested\b/i.test(o.label))
  return a && b ? { A: a.key, B: b.key } : null
}

/** The `diagrams` field of a run's detail: where the three files are (relative to the repo root) and the review counts. null when the dossier has no visual review. */
export function diagramsOf(root: string, dossier: string): { dir: string; l0: string | null; l1: string | null; l2: string | null; arch: string[]; review: Summary & { exists: boolean } } | null {
  const rel = join(".discovery", "visual-review", dossier)
  const dir = join(root, rel)
  const f = (v: string) => (existsSync(join(dir, v + ".json")) ? join(rel, v + ".json") : null)
  const l0 = f("l0"), l1 = f("l1"), l2 = f("l2")
  if (!l0 && !l1 && !l2) return null
  const st = reviewState(dir, dossier)
  return { dir: rel, l0, l1, l2, arch: archStems(dir), review: { ...st.summary, exists: st.exists } }
}

/** The Intent tab of the review canvas: what the dossier wants to accomplish, as context before the diagrams. */
export type Intent = { title: string; text: string; file: string }
export const MAX_INTENT = 4000

/** The title in the front matter of the dossier file of `dossier` under `root` (`<ID>-<slug>.md` or `<ID>.md`); "" when none. */
function dossierTitle(root: string, dossier: string): string {
  const dir = join(root, ".discovery", "dossiers")
  try {
    const n = readdirSync(dir).filter((x) => x === dossier + ".md" || (x.startsWith(dossier + "-") && x.endsWith(".md"))).sort()[0]
    if (!n) return ""
    const fm = /^---\n([\s\S]*?)\n---/.exec(readFileSync(join(dir, n), "utf8"))
    const t = fm && /^title:\s*(.+)$/m.exec(fm[1])
    return t ? t[1].trim().replace(/^(["'])(.*)\1$/, "$2") : ""
  } catch { return "" }
}

/**
 * The intent of a dossier for the Intent tab: `.discovery/visual-review/<ID>/intent.md`, the short plain summary the run
 * writes with the diagrams (what the change sets out to accomplish), with the dossier's title. null when the run wrote none.
 */
export function dossierIntent(root: string, dir: string, dossier: string): Intent | null {
  const own = join(dir, "intent.md")
  try {
    if (!statSync(own).isFile() || statSync(own).size > 4 * MAX_INTENT) return null
    const t = readFileSync(own, "utf8").trim()
    return t ? { title: dossierTitle(root, dossier), text: clip(t, MAX_INTENT), file: "intent.md" } : null
  } catch { return null }
}
