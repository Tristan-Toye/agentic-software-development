// Visual review (VR-04): the owner's verdict per diagram element, kept in .discovery/visual-review/<ID>/review.json
// (shape: references/visual-review/review.schema.json), and the answer text built from them for the one product question.
// Pure file work, no server state: the routes in server.ts call these.
import { existsSync, mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { graphHash, VR_VIEWS } from "./vrlayout.ts"

export const VERDICTS = ["ok", "question", "change", "remove"] as const
export type Verdict = (typeof VERDICTS)[number]
export type Diagram = (typeof VR_VIEWS)[number]
export type Anchor = { diagram: Diagram; element: string; graph_hash: string }
export type ReviewLine = { anchor: Anchor; verdict: Verdict; comment: string; ts: string }
export type Review = { schema_version: "1.0.0"; dossier: string; updated: string; lines: ReviewLine[] }
/** current: made on the graph as it is now. outdated: made on an older graph. gone: the element is no longer in the diagram. */
export type LineState = "current" | "outdated" | "gone"
export type StateLine = ReviewLine & { state: LineState }
export type Summary = { total: number; checked: number; ok: number; question: number; change: number; remove: number; outdated: number; gone: number; unchecked: number; text: string }
export type ReviewState = { exists: boolean; hashes: Partial<Record<Diagram, string>>; lines: StateLine[]; summary: Summary; unchecked: { diagram: Diagram; element: string }[] }

export const MAX_COMMENT = 1000
export const MAX_ID = 200
export const MAX_BODY = 8 * 1024
export const MAX_LINES = 600
export const MAX_FILE = 512 * 1024
/** The store cuts a line at 3500 characters; an answer stays well under it. */
export const MAX_ANSWER = 3300

export class ReviewError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

export const reviewFile = (dir: string): string => join(dir, "review.json")

type Graphs = Partial<Record<Diagram, { hash: string; nodes: Set<string>; labels: Map<string, string> }>>
/** The nodes (the elements a verdict can be about) and the hash of each diagram file that exists and parses. */
export function readGraphs(dir: string): Graphs {
  const out: Graphs = {}
  for (const v of VR_VIEWS) {
    const file = join(dir, v.toLowerCase() + ".json")
    try {
      if (!existsSync(file) || !statSync(file).isFile()) continue
      const g = JSON.parse(readFileSync(file, "utf8"))
      if (!g || !Array.isArray(g.nodes)) continue
      const labels = new Map<string, string>()
      for (const n of g.nodes) if (n && typeof n.id === "string") labels.set(n.id, typeof n.label === "string" ? n.label : n.id)
      out[v] = { hash: graphHash(g), nodes: new Set(labels.keys()), labels }
    } catch { /* an unreadable diagram has no elements */ }
  }
  return out
}

/** The lines of review.json that have the right shape; anything else in the file is ignored (and rewritten away on the next save). */
export function readReview(dir: string, dossier: string): Review | null {
  const file = reviewFile(dir)
  try {
    if (!existsSync(file) || statSync(file).size > MAX_FILE) return null
    const o = JSON.parse(readFileSync(file, "utf8"))
    if (!o || typeof o !== "object" || !Array.isArray(o.lines)) return null
    const lines: ReviewLine[] = []
    for (const l of o.lines) {
      const a = l?.anchor
      if (a && VR_VIEWS.includes(a.diagram) && typeof a.element === "string" && typeof a.graph_hash === "string" && (VERDICTS as readonly string[]).includes(l.verdict))
        lines.push({ anchor: { diagram: a.diagram, element: a.element, graph_hash: a.graph_hash }, verdict: l.verdict, comment: typeof l.comment === "string" ? l.comment : "", ts: typeof l.ts === "string" ? l.ts : "" })
    }
    return { schema_version: "1.0.0", dossier: typeof o.dossier === "string" ? o.dossier : dossier, updated: typeof o.updated === "string" ? o.updated : "", lines }
  } catch { return null }
}

/** The verdicts against the diagrams as they are now, and the count line ("12 of 14 elements checked"). Outdated and gone lines are kept, not counted. */
export function reviewState(dir: string, dossier: string): ReviewState {
  const graphs = readGraphs(dir)
  const review = readReview(dir, dossier)
  const lines: StateLine[] = (review?.lines ?? []).map((l) => {
    const g = graphs[l.anchor.diagram]
    const state: LineState = !g || !g.nodes.has(l.anchor.element) ? "gone" : g.hash !== l.anchor.graph_hash ? "outdated" : "current"
    return { ...l, state }
  })
  const cur = new Map(lines.filter((l) => l.state === "current").map((l) => [l.anchor.diagram + "\u0000" + l.anchor.element, l]))
  const unchecked: { diagram: Diagram; element: string }[] = []
  let total = 0
  for (const v of VR_VIEWS) for (const id of graphs[v]?.nodes ?? []) { total++; if (!cur.has(v + "\u0000" + id)) unchecked.push({ diagram: v, element: id }) }
  const n = (k: Verdict) => [...cur.values()].filter((l) => l.verdict === k).length
  const s = { total, checked: cur.size, ok: n("ok"), question: n("question"), change: n("change"), remove: n("remove"), outdated: lines.filter((l) => l.state === "outdated").length, gone: lines.filter((l) => l.state === "gone").length, unchecked: unchecked.length }
  const bits = [s.question && s.question + " question" + (s.question > 1 ? "s" : ""), s.change && s.change + " change" + (s.change > 1 ? "s" : ""), s.remove && s.remove + " to remove", s.outdated && s.outdated + " outdated"].filter(Boolean)
  const text = s.checked + " of " + s.total + " elements checked" + (bits.length ? ", " + bits.join(", ") : "")
  return { exists: review !== null, hashes: Object.fromEntries(VR_VIEWS.filter((v) => graphs[v]).map((v) => [v, graphs[v]!.hash])), lines, summary: { ...s, text }, unchecked }
}

export type VerdictInput = { diagram: unknown; element: unknown; verdict: unknown; comment?: unknown }
/**
 * Upsert (or clear, with verdict "unset") one verdict. Refuses an unknown diagram, an element that is not a node of the
 * current graph, an unknown verdict and an oversize comment. `followup` is set when the line is a Question with a comment:
 * the route sends it to the agent as a follow-up that carries the same anchor.
 */
export function setVerdict(dir: string, dossier: string, b: VerdictInput): { state: ReviewState; followup: { anchor: Anchor; text: string } | null } {
  if (typeof b.diagram !== "string" || !(VR_VIEWS as readonly string[]).includes(b.diagram)) throw new ReviewError(400, "diagram must be L0, L1 or L2")
  const diagram = b.diagram as Diagram
  if (typeof b.element !== "string" || !b.element || b.element.length > MAX_ID) throw new ReviewError(400, "element must be an id of at most " + MAX_ID + " characters")
  const unset = b.verdict === "unset"
  if (!unset && !(VERDICTS as readonly string[]).includes(b.verdict as string)) throw new ReviewError(400, "verdict must be one of " + VERDICTS.join(", ") + " or unset")
  const comment = b.comment === undefined || b.comment === null ? "" : b.comment
  if (typeof comment !== "string") throw new ReviewError(400, "comment must be text")
  if (comment.length > MAX_COMMENT) throw new ReviewError(413, "comment is longer than " + MAX_COMMENT + " characters")
  const g = readGraphs(dir)[diagram]
  if (!g) throw new ReviewError(404, "no " + diagram + " diagram for " + dossier)
  if (!g.nodes.has(b.element)) throw new ReviewError(400, "unknown element " + b.element + " in " + diagram)
  const prev = readReview(dir, dossier) ?? { schema_version: "1.0.0" as const, dossier, updated: "", lines: [] }
  const same = (l: ReviewLine) => l.anchor.diagram === diagram && l.anchor.element === b.element
  const old = prev.lines.find(same)
  const lines = prev.lines.filter((l) => !same(l))
  const ts = new Date().toISOString()
  let followup: { anchor: Anchor; text: string } | null = null
  if (!unset) {
    const anchor: Anchor = { diagram, element: b.element, graph_hash: g.hash }
    lines.push({ anchor, verdict: b.verdict as Verdict, comment: comment.trim(), ts })
    if (b.verdict === "question" && comment.trim() && !(old && old.verdict === "question" && old.comment === comment.trim() && old.anchor.graph_hash === g.hash)) followup = { anchor, text: comment.trim() }
  }
  if (lines.length > MAX_LINES) throw new ReviewError(413, "review.json would hold more than " + MAX_LINES + " verdicts")
  const body = JSON.stringify({ schema_version: "1.0.0", dossier, updated: ts, lines }, null, 1) + "\n"
  if (body.length > MAX_FILE) throw new ReviewError(413, "review.json would be larger than " + MAX_FILE + " bytes")
  mkdirSync(dir, { recursive: true })
  const tmp = reviewFile(dir) + "." + process.pid + ".tmp"
  writeFileSync(tmp, body)
  renameSync(tmp, reviewFile(dir))
  return { state: reviewState(dir, dossier), followup }
}

const clip = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s)
/**
 * The answer for the visual-review question, as the Phase 3 step defines it. Option A (Approved) only when every element
 * has a current OK. Otherwise option B (Changes requested), with one line per flagged element: verdict, diagram, element
 * id, comment. Elements not yet looked at and verdicts made on an older graph are named; review.json holds the full text.
 */
export function buildAnswer(state: ReviewState, dossier: string): { option: "A" | "B"; text: string } {
  const s = state.summary
  const head = (dossier ? dossier + " " : "") + "visual review: "
  if (s.total > 0 && s.ok === s.total) return { option: "A", text: head + "approved, all " + s.total + " elements OK." }
  const label: Record<Verdict, string> = { ok: "ok", question: "question", change: "change", remove: "remove from scope" }
  const flagged = state.lines.filter((l) => l.state === "current" && l.verdict !== "ok")
  const out = [head + "changes requested. " + s.text + ". Full verdicts: .discovery/visual-review/" + dossier + "/review.json"]
  for (const l of flagged) out.push("- " + label[l.verdict] + " " + l.anchor.diagram + " " + l.anchor.element + (l.comment ? ": " + clip(l.comment.replace(/\s+/g, " "), 240) : ""))
  const old = state.lines.filter((l) => l.state === "outdated")
  if (old.length) out.push("- outdated (made on an older graph): " + old.map((l) => l.anchor.diagram + " " + l.anchor.element).join(", "))
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

/** The option keys of the question for "Approved" and "Changes requested"; null when the question is not the visual-review one. */
export function reviewOptions(options: { key: string; label: string }[]): { A: string; B: string } | null {
  const a = options.find((o) => /^approved\b/i.test(o.label)), b = options.find((o) => /^changes requested\b/i.test(o.label))
  return a && b ? { A: a.key, B: b.key } : null
}

/** The `diagrams` field of a run's detail: where the three files are (relative to the repo root) and the review counts. null when the dossier has no visual review. */
export function diagramsOf(root: string, dossier: string): { dir: string; l0: string | null; l1: string | null; l2: string | null; review: Summary & { exists: boolean } } | null {
  const rel = join(".discovery", "visual-review", dossier)
  const dir = join(root, rel)
  const f = (v: string) => (existsSync(join(dir, v + ".json")) ? join(rel, v + ".json") : null)
  const l0 = f("l0"), l1 = f("l1"), l2 = f("l2")
  if (!l0 && !l1 && !l2) return null
  const st = reviewState(dir, dossier)
  return { dir: rel, l0, l1, l2, review: { ...st.summary, exists: st.exists } }
}
