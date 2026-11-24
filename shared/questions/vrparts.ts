// The parts of a visual review, always all listed (L0, L1, L2, decision flow, sequence, architecture affected, architecture new):
// each is real content (a diagram tab) or a stated `none - <reason>` card. Read from `.discovery/visual-review/<ID>/` (or its
// `preview-all/` preview) on the server: parts.json holds the author's reasons, the files hold the content. A review written
// before parts.json existed still lists every part; a missing one says it was not drawn. Also the comment boxes of the preview
// view ("All diagrams (preview)"): comments.json beside the preview, never review.json (a preview is never an approval).
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { archStems, DECISION_VIEW, hasDecision, PREVIEW_PREFIX, previewStems } from "./vrlayout.ts"

export const PREVIEW_ALL_DIR = "preview-all"

export type PartEntry = {
  key: string
  label: string
  kind: "content" | "none"
  /** The reason of a `none` card, or the note of a content card. */
  reason?: string
  /** The canvas tabs that show this part's content (empty for a `none` card). */
  views: string[]
}

const readJson = (file: string): any => { try { return JSON.parse(readFileSync(file, "utf8")) } catch { return null } }
const text = (v: unknown): string => (typeof v === "string" ? v.trim() : "")
const LEGACY = "this review was written before the parts were mandatory: nothing was recorded for this part"

/** Every part of the review in `dir`, in reading order. Never throws; a part with neither content nor reason is a `none` card saying so. */
export function readParts(dir: string): PartEntry[] {
  const parts = readJson(join(dir, "parts.json")) ?? {}
  const out: PartEntry[] = []
  const reasonOf = (k: string): string => text(parts[k]) || LEGACY
  for (const [k, label] of [["L0", "L0 features"], ["L1", "L1 flow"], ["L2", "L2 nodes"]] as const) {
    const has = existsSync(join(dir, k.toLowerCase() + ".json"))
    out.push({ key: k, label, kind: has ? "content" : "none", views: has ? [k] : [], ...(has ? {} : { reason: "not drawn: " + k.toLowerCase() + ".json is missing" }) })
  }
  out.push(hasDecision(dir)
    ? { key: "decision", label: "Decision flow", kind: "content", views: [DECISION_VIEW] }
    : { key: "decision", label: "Decision flow", kind: "none", views: [], reason: reasonOf("decision_none") })
  const seq = [...previewStems(dir).map((s) => PREVIEW_PREFIX + s)]
  const drafts = archStems(dir)
  const meta = (s: string): any => (readJson(join(dir, "arch", s + ".json")) ?? {}).meta ?? {}
  const isUpdate = (s: string): boolean => meta(s).updates_existing === true
  const isSeq = (s: string): boolean => (readJson(join(dir, "arch", s + ".json")) ?? {}).view === "seq"
  const seqArch = drafts.filter((s) => isSeq(s) && !isUpdate(s)).map((s) => "arch:" + s)
  out.push(seq.length || seqArch.length
    ? { key: "sequence", label: "Sequence", kind: "content", views: [...seq, ...seqArch], reason: "on demand: open it with the Sequence diagrams button" }
    : { key: "sequence", label: "Sequence", kind: "none", views: [], reason: reasonOf("sequence_none") })
  const updates = drafts.filter(isUpdate)
  const unchanged: any[] = Array.isArray(parts.unchanged) ? parts.unchanged.filter((u: any) => u && typeof u.stem === "string") : []
  for (const s of updates) out.push({ key: "affected:" + s, label: "Architecture impact, affected: " + s, kind: "content", views: ["arch:" + s] })
  for (const u of unchanged) out.push({ key: "affected:" + u.stem, label: "Architecture impact, affected: " + u.stem, kind: "none", views: [], reason: "Diagram " + u.stem + " unchanged: " + text(u.reason) })
  if (!updates.length && !unchanged.length) out.push({ key: "affected", label: "Architecture impact, affected", kind: "none", views: [], reason: reasonOf("affected_none") })
  const fresh = drafts.filter((s) => !isUpdate(s))
  for (const s of fresh) out.push({ key: "new:" + s, label: "Architecture impact, new: " + s, kind: "content", views: ["arch:" + s], ...(text(parts.new_note) ? { reason: text(parts.new_note) } : {}) })
  if (!fresh.length) out.push({ key: "new", label: "Architecture impact, new", kind: "none", views: [], reason: reasonOf("new_none") + (text(parts.new_note) ? " (note to the owner: " + text(parts.new_note) + ")" : "") })
  return out
}

const COMMENTS = "comments.json"
const PART_RE = /^[\w.:-]{1,120}$/
export const MAX_COMMENT = 4000

/** The comments of a preview directory: {part key: {comment, at}}. */
export function readPreviewComments(dir: string): Record<string, { comment: string; at: string }> {
  const o = readJson(join(dir, COMMENTS))
  return o && typeof o === "object" && !Array.isArray(o) ? o : {}
}

/** Save (or, for an empty comment, clear) the comment of one part of a preview directory. Throws an Error with a plain message on bad input. */
export function writePreviewComment(dir: string, part: unknown, comment: unknown): Record<string, { comment: string; at: string }> {
  if (typeof part !== "string" || !PART_RE.test(part)) throw new Error("part must be a plain key")
  if (typeof comment !== "string" || comment.length > MAX_COMMENT) throw new Error("comment must be a text of at most " + MAX_COMMENT + " characters")
  if (!existsSync(join(dir, "PREVIEW-ONLY"))) throw new Error("not a preview directory: comments are kept only beside a preview (vr_preview_all.py)")
  const all = readPreviewComments(dir)
  if (comment.trim()) all[part] = { comment: comment.trim(), at: new Date().toISOString() }
  else delete all[part]
  mkdirSync(dir, { recursive: true })
  const tmp = join(dir, COMMENTS + ".tmp")
  writeFileSync(tmp, JSON.stringify(all, null, 2) + "\n")
  renameSync(tmp, join(dir, COMMENTS))
  return all
}
