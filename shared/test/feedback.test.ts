// Unified feedback control: the local draft (feedback.ts), the notes and viewed marks of review.json (vrreview.ts), and that the
// page ships the control. Run: bun test shared/test/feedback.test.ts
import { describe, expect, test } from "bun:test"
import { cpSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs"
import { tmpdir } from "node:os"
import { join } from "node:path"
import { fbCounts, fbEmpty, fbIsViewed, fbNote, fbPayload, fbPending, fbRead, fbSetNote, fbSetViewed, fbSettle, fbWrite } from "../questions/feedback.ts"
import { approveAll, buildAnswer, isAssetAnchor, readReview, reviewState, setVerdict, submitFeedback, ReviewError } from "../questions/vrreview.ts"
import { PAGE } from "../questions/page.ts"

const FIX = join(import.meta.dir, "..", "..", "references", "visual-review", "fixtures")
const dirWith = (): string => {
  const d = mkdtempSync(join(tmpdir(), "fb-"))
  for (const v of ["l0", "l1", "l2"]) cpSync(join(FIX, `w145-${v}.json`), join(d, `${v}.json`))
  return d
}
const fails = (f: () => unknown): ReviewError => { try { f() } catch (e) { return e as ReviewError } throw new Error("did not throw") }
const mem = () => { const m = new Map<string, string>(); return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k), m } }

describe("draft state", () => {
  test("a stored draft round-trips, an empty one removes the item, bad or missing storage gives an empty draft and never throws", () => {
    const s = mem(), st = fbSetViewed(fbSetNote(fbEmpty(), "doc:problem", { verdict: "change", comment: "x", blocking: true }), "summary", true)
    expect(fbWrite(s, "r/W-1", st)).toBe(true)
    expect(fbRead(s, "r/W-1")).toEqual(st)
    fbWrite(s, "r/W-1", fbEmpty())
    expect(s.m.size).toBe(0)
    s.setItem("asd.fb.r/W-2", "not json")
    expect(fbRead(s, "r/W-2")).toEqual(fbEmpty())
    expect(fbRead(null, "x")).toEqual(fbEmpty())
    expect(fbWrite(null, "x", st)).toBe(false)
    expect(fbRead({ getItem: () => { throw new Error("blocked") } }, "x")).toEqual(fbEmpty())
    expect(fbWrite({ setItem: () => { throw new Error("full") }, removeItem: () => {} }, "x", st)).toBe(false)
  })
  test("N of M viewed: the draft choice wins over review.json, and a viewed tick is never required", () => {
    const saved = { notes: [], viewed: ["summary"] }, st = fbEmpty()
    const a = ["summary", "doc:problem", "doc:approach"]
    expect(fbCounts(a, saved, st).text).toBe("1 of 3 viewed")
    fbSetViewed(st, "doc:problem", true); fbSetViewed(st, "summary", false)
    expect(fbCounts(a, saved, st)).toMatchObject({ viewed: 1, total: 3 })
    expect(fbIsViewed(saved, st, "summary")).toBe(false)
  })
  test("fbNote shows the draft over the saved note", () => {
    const saved = { notes: [{ anchor: "summary", verdict: "question", comment: "why", blocking: true }] }
    expect(fbNote(saved, fbEmpty(), "summary")).toMatchObject({ verdict: "question", draft: false, blocking: true })
    expect(fbNote(saved, fbSetNote(fbEmpty(), "summary", { verdict: "ok" }), "summary")).toMatchObject({ verdict: "ok", draft: true })
    expect(fbNote(saved, fbEmpty(), "doc:x")).toMatchObject({ verdict: "", draft: false })
  })
  test("fbPayload sends only what differs from review.json; clearing a saved note sends clear; an empty draft sends nothing", () => {
    const saved = { notes: [{ anchor: "summary", verdict: "ok", comment: "", blocking: false }], viewed: ["summary"] }
    expect(fbPayload(saved, fbEmpty())).toBeNull()
    const st = fbEmpty()
    fbSetNote(st, "summary", { verdict: "ok" }); fbSetViewed(st, "summary", true)
    expect(fbPayload(saved, st)).toBeNull()
    fbSetNote(st, "doc:a", { verdict: "change", comment: " fix it ", blocking: true }); fbSetViewed(st, "doc:a", true); fbSetViewed(st, "summary", false)
    expect(fbPayload(saved, st)).toEqual({ notes: [{ anchor: "doc:a", verdict: "change", comment: "fix it", blocking: true }], viewed_add: ["doc:a"], viewed_remove: ["summary"] })
    expect(fbPending(saved, st)).toBe(3)
    fbSetNote(st, "summary", { verdict: "", comment: "" })
    expect(fbPayload(saved, st)!.notes).toContainEqual({ anchor: "summary", verdict: "clear" })
    expect(fbPayload({}, fbSetNote(fbEmpty(), "doc:a", { comment: "just a remark" }))!.notes[0].verdict).toBe("comment")
  })
  test("fbSettle drops what was sent and keeps what was typed since", () => {
    const st = fbSetViewed(fbSetNote(fbEmpty(), "doc:a", { verdict: "question", comment: "q" }), "doc:a", true)
    const sent = fbPayload({}, st)
    fbSetNote(st, "doc:b", { verdict: "ok" })
    fbSettle(st, sent)
    expect(Object.keys(st.drafts)).toEqual(["doc:b"])
    expect(st.viewed).toEqual({})
  })
})

describe("submitFeedback and review.json", () => {
  test("anchors: summary and the asset prefixes only", () => {
    for (const a of ["summary", "doc:problem", "adr:ADR-0007", "deferred:D-3", "pane:diagram", "el:L1:node.a:b"]) expect(isAssetAnchor(a)).toBe(true)
    for (const a of ["", "doc:", "doc", "L1:x", "doc:a b", "summary:x", 5, "doc:../x"]) expect(isAssetAnchor(a)).toBe(false)
  })
  test("notes and viewed are written next to the lines, upserted by anchor, cleared on clear, and unrelated lines are kept", () => {
    const d = dirWith()
    const id = JSON.parse(readFileSync(join(d, "l1.json"), "utf8")).nodes[0].id
    setVerdict(d, "W-1", { diagram: "L1", element: id, verdict: "ok" })
    let r = submitFeedback(d, "W-1", { notes: [{ anchor: "doc:problem", verdict: "question", comment: " why? ", blocking: true }, { anchor: "summary", verdict: "ok" }], viewed_add: ["summary", "doc:problem"] })
    const f = JSON.parse(readFileSync(join(d, "review.json"), "utf8"))
    expect(f.lines).toHaveLength(1)
    expect(f.notes.map((n: any) => [n.anchor, n.verdict, n.comment, n.blocking])).toEqual([["doc:problem", "question", "why?", true], ["summary", "ok", "", false]])
    expect(f.viewed).toEqual(["summary", "doc:problem"])
    expect(r.followups.map((n) => n.anchor)).toEqual(["doc:problem"])
    r = submitFeedback(d, "W-1", { notes: [{ anchor: "doc:problem", verdict: "question", comment: "why?", blocking: true }] })
    expect(r.followups).toEqual([])   // unchanged: not sent to the agent twice
    r = submitFeedback(d, "W-1", { notes: [{ anchor: "doc:problem", verdict: "clear" }], viewed_remove: ["summary"] })
    expect(r.state.notes.map((n) => n.anchor)).toEqual(["summary"])
    expect(r.state.viewed).toEqual(["doc:problem"])
    expect(r.state.lines).toHaveLength(1)
  })
  test("an informing comment or a question reaches the agent; an OK or a bare viewed does not", () => {
    const d = dirWith()
    const r = submitFeedback(d, "W-1", { notes: [{ anchor: "summary", verdict: "ok" }, { anchor: "doc:a", verdict: "comment", comment: "note" }, { anchor: "doc:b", verdict: "comment" }, { anchor: "doc:c", verdict: "change", comment: "" }] })
    expect(r.followups.map((n) => n.anchor)).toEqual(["doc:a", "doc:c"])
  })
  test("invalid input is refused with nothing written", () => {
    const d = dirWith()
    expect(fails(() => submitFeedback(d, "W-1", { notes: [{ anchor: "L1:x", verdict: "ok" }] })).status).toBe(400)
    expect(fails(() => submitFeedback(d, "W-1", { notes: [{ anchor: "summary", verdict: "remove" }] })).status).toBe(400)
    expect(fails(() => submitFeedback(d, "W-1", { notes: [{ anchor: "summary", verdict: "ok", comment: "x".repeat(1001) }] })).status).toBe(413)
    expect(fails(() => submitFeedback(d, "W-1", { notes: "no" })).status).toBe(400)
    expect(fails(() => submitFeedback(d, "W-1", { viewed_add: ["bad anchor"] })).status).toBe(400)
    expect(readReview(d, "W-1")).toBeNull()
  })
  test("works with no diagram files at all (a dossier without a visual review)", () => {
    const d = mkdtempSync(join(tmpdir(), "fb-"))
    const r = submitFeedback(d, "W-2", { viewed_add: ["doc:problem"] })
    expect(r.state.viewed).toEqual(["doc:problem"])
    expect(r.state.summary.total).toBe(0)
  })
  test("a verdict save and the one-click approval keep the notes and viewed marks; a blocking line is stored and named in the answer", () => {
    const d = dirWith()
    submitFeedback(d, "W-1", { notes: [{ anchor: "summary", verdict: "comment", comment: "c" }], viewed_add: ["summary"] })
    const id = JSON.parse(readFileSync(join(d, "l1.json"), "utf8")).nodes[0].id
    const s = setVerdict(d, "W-1", { diagram: "L1", element: id, verdict: "change", comment: "no", blocking: true }).state
    expect(s.notes).toHaveLength(1)
    expect(s.viewed).toEqual(["summary"])
    expect(s.lines[0].blocking).toBe(true)
    expect(buildAnswer(s, "W-1").text).toContain("BLOCKING change L1 " + id)
    setVerdict(d, "W-1", { diagram: "L1", element: id, verdict: "unset" })
    expect(approveAll(d, "W-1").notes).toHaveLength(1)
  })
  test("a blocking Question or Change note holds the approval back; an informing one does not", () => {
    const d = dirWith()
    approveAll(d, "W-1")
    expect(buildAnswer(reviewState(d, "W-1"), "W-1").option).toBe("A")
    submitFeedback(d, "W-1", { notes: [{ anchor: "doc:a", verdict: "question", comment: "q", blocking: false }] })
    expect(buildAnswer(reviewState(d, "W-1"), "W-1").option).toBe("A")
    submitFeedback(d, "W-1", { notes: [{ anchor: "doc:a", verdict: "question", comment: "q", blocking: true }] })
    const a = buildAnswer(reviewState(d, "W-1"), "W-1")
    expect(a.option).toBe("B")
    expect(a.text).toContain("BLOCKING question on doc:a")
    expect(fails(() => approveAll(d, "W-1")).status).toBe(409)
  })
  test("back-compat: an old review.json without notes or viewed loads, and is written back without them", () => {
    const d = dirWith()
    const id = JSON.parse(readFileSync(join(d, "l1.json"), "utf8")).nodes[0].id
    setVerdict(d, "W-1", { diagram: "L1", element: id, verdict: "ok" })
    const old = JSON.parse(readFileSync(join(d, "review.json"), "utf8"))
    expect(Object.keys(old)).toEqual(["schema_version", "dossier", "updated", "lines"])
    expect(old.lines[0].blocking).toBeUndefined()
    const rv = reviewState(d, "W-1")
    expect(rv.notes).toEqual([]); expect(rv.viewed).toEqual([])
    setVerdict(d, "W-1", { diagram: "L1", element: id, verdict: "question", comment: "q" })
    expect(Object.keys(JSON.parse(readFileSync(join(d, "review.json"), "utf8")))).toEqual(["schema_version", "dossier", "updated", "lines"])
    writeFileSync(join(d, "review.json"), JSON.stringify({ ...old, notes: [{ anchor: "bad anchor", verdict: "ok" }, { anchor: "summary", verdict: "nope" }], viewed: ["summary", 4, "summary"] }))
    expect(readReview(d, "W-1")!.notes).toBeUndefined()
    expect(readReview(d, "W-1")!.viewed).toEqual(["summary"])
  })
})

describe("the page ships the control", () => {
  test("the draft functions and the feedback route are in the page", () => {
    for (const w of ["function fbPayload", "function fbCounts", "function fbBar", "/api/visual-review/feedback", "data-act': 'fb-send'", "fb: 'summary'", "'doc:' + s.slug", "'el:' + st.view"]) expect(PAGE).toContain(w)
  })
  test("the shipped page script parses", () => {
    const m = /<script>([\s\S]*)<\/script>/.exec(PAGE)
    expect(m).not.toBeNull()
    expect(() => new Function(m![1])).not.toThrow()
  })
})
